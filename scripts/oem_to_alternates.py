"""One-off: turn the per-unit "OEM part number" info items into alternates of
the part they belong to.

    python scripts/oem_to_alternates.py path/to/fleet.sqlite --xlsx "Filters Cloud - Reconciled.xlsx"            # dry run
    python scripts/oem_to_alternates.py path/to/fleet.sqlite --xlsx "Filters Cloud - Reconciled.xlsx" --apply    # write it

The old filter import saved OEM Number/Brand as a per-unit info item and
threw away which part it was for. The spreadsheet still has that pairing
(Unit / Main P/N / OEM Number / OEM Brand), so each info item is matched to
a part like this: find the spreadsheet row(s) for the same unit and OEM
number, take the row's Main P/N (or, like the importer, its first brand
column when Main P/N is blank), and look for a part with that number linked
to that unit. Only when exactly one part comes out is the OEM number added as
an alternate (note = the OEM brand, e.g. "Ford") and the info item deleted.

Anything that doesn't pair cleanly -- not on the sheet for that unit, the
sheet's part isn't linked to the unit any more (renumbered since), or it
points at more than one part -- is left exactly as it is and listed at the
end, along with the parts linked to that unit, so it can be decided by hand.

A dry run does everything inside a transaction, prints it, and rolls back.
--apply first copies the database to <db>.before-oem-<timestamp>, then
commits. Needs Python and openpyxl (the app's venv has it).
"""
import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

import openpyxl

BRAND_COLUMNS = ("fram", "donaldson", "fleetguard", "baldwin", "napa")
LABEL = "OEM part number"


def split_value(value):
    """'FL2124S (Ford)' -> ('FL2124S', 'Ford'); no brackets -> (value, None)."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", value)
    return (m.group(1).strip(), m.group(2).strip() or None) if m else (value.strip(), None)


def read_sheet(path):
    """{unit: [(main_pn, oem_number)]} from the Active List sheet, applying the
    importer's rule that a blank Main P/N falls back to the first brand column."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Active List"]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip().lower() if h is not None else "" for h in next(rows)]
    col = {h: i for i, h in enumerate(header) if h}

    def cell(row, name):
        i = col.get(name)
        if i is None or i >= len(row) or row[i] in (None, ""):
            return None
        return str(row[i]).strip()

    sheet = defaultdict(list)
    for row in rows:
        unit = cell(row, "unit")
        if not unit or unit.lower() == "false":
            continue
        unit = unit.zfill(3) if unit.isdigit() else unit
        main = cell(row, "main p/n")
        if not main:
            main = next((cell(row, b) for b in BRAND_COLUMNS if cell(row, b)), None)
        oem = cell(row, "oem number")
        if oem:
            sheet[unit].append((main, oem))
    return sheet


def run(db, sheet):
    log, problems = [], []
    info_items = db.execute(
        """SELECT i.id, i.equipment_id, e.unit_number, i.value FROM equipment_info_items i
           JOIN equipment e ON e.id = i.equipment_id WHERE i.label = ? ORDER BY e.unit_number, i.id""",
        (LABEL,),
    ).fetchall()

    linked = defaultdict(list)
    for r in db.execute(
        """SELECT l.equipment_id, ci.id, ci.value, ci.label FROM equipment_catalog_items l
           JOIN catalog_items ci ON ci.id = l.catalog_item_id"""
    ):
        linked[r["equipment_id"]].append(r)

    for item in info_items:
        number, brand = split_value(item["value"])
        rows = [r for r in sheet.get(item["unit_number"], []) if r[1].upper() == number.upper()]
        parts = {}
        for main, _oem in rows:
            for p in linked[item["equipment_id"]]:
                if main and p["value"] and p["value"].upper() == main.upper():
                    parts[p["id"]] = p
        if len(parts) != 1:
            if not rows:
                why = "not on the spreadsheet for this unit"
            elif not parts:
                why = f"spreadsheet part {sorted({m for m, _ in rows if m}) or '(none)'} isn't linked to this unit"
            else:
                why = "pairs with more than one part: " + ", ".join(sorted(p["value"] for p in parts.values()))
            context = "; ".join(f"{p['label']} {p['value']}" for p in linked[item["equipment_id"]]) or "no parts linked"
            problems.append((item["unit_number"], item["value"], why, context))
            continue

        part = next(iter(parts.values()))
        existing = {r["value"].strip().upper() for r in db.execute(
            "SELECT value FROM catalog_item_alternates WHERE catalog_item_id = ?", (part["id"],))}
        if number.upper() == (part["value"] or "").upper() or number.upper() in existing:
            log.append(f"{item['unit_number']}: {number} is already on {part['value']}; dropped the duplicate info item")
        else:
            db.execute(
                "INSERT INTO catalog_item_alternates (catalog_item_id, value, notes) VALUES (?, ?, ?)",
                (part["id"], number, brand or "OEM"),
            )
            log.append(f"{item['unit_number']}: {number} ({brand or 'OEM'}) -> alternate of {part['label']} {part['value']}")
        db.execute("DELETE FROM equipment_info_items WHERE id = ?", (item["id"],))
    return log, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database")
    parser.add_argument("--xlsx", required=True, help="the filter spreadsheet (needs an 'Active List' sheet)")
    parser.add_argument("--apply", action="store_true", help="commit the changes (default: dry run)")
    args = parser.parse_args()

    db = sqlite3.connect(args.database, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("ABORT: database failed integrity_check")

    sheet = read_sheet(args.xlsx)

    if args.apply:
        backup_path = f"{args.database}.before-oem-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        with sqlite3.connect(backup_path) as dest:
            db.backup(dest)
        print(f"Backed up to {backup_path}")

    db.execute("BEGIN IMMEDIATE")
    try:
        log, problems = run(db, sheet)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign_key_check found dangling references")
    except Exception:
        db.execute("ROLLBACK")
        raise

    for line in log:
        print(line)
    print(f"\nMatched {len(log)} OEM numbers.")
    if problems:
        print(f"\n{len(problems)} could not be matched safely and were left as they are:")
        for unit, value, why, context in problems:
            print(f"  {unit}  {value}\n      {why}\n      unit's parts: {context}")

    if args.apply:
        db.execute("COMMIT")
        print("\nApplied.")
    else:
        db.execute("ROLLBACK")
        print("\nDry run only. Nothing was written; rerun with --apply.")


if __name__ == "__main__":
    main()
