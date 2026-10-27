"""One-off: apply the filter-spreadsheet reconciliation agreed on 2026-10-26
to an EsoFleet database.

    python scripts/reconcile_2026_10_26.py path/to/fleet.sqlite            # dry run
    python scripts/reconcile_2026_10_26.py path/to/fleet.sqlite --apply    # write it

A dry run makes every change inside a transaction, prints what it did, and
rolls back. --apply first copies the database to
<db>.before-reconcile-<timestamp> and then commits.

Every step looks rows up by part number / unit number (never by row id)
and checks they're still in the state that was reviewed, so if production
has been edited since the 2026-10-26 backup the script stops with
"ABORT: ..." before changing anything. Standalone: needs only Python.

Not included, still waiting on a physical check: whether 080 gets the
MIU805005 / MIU802421 fuel links.
"""
import argparse
import sqlite3
import sys
from datetime import datetime

LABELS = {
    "oil": "Oil filter", "air": "Air filter", "air inner": "Air filter (inner)",
    "fuel": "Fuel filter", "hydraulic": "Hydraulic filter", "cabin": "Cabin filter",
    "breather": "Breather filter", "transmission": "Transmission filter",
}


class Abort(Exception):
    pass


class Fleet:
    def __init__(self, db):
        self.db = db
        self.log = []

    def say(self, msg):
        self.log.append(msg)
        print("  " + msg)

    # ---- lookups -------------------------------------------------------
    def one(self, sql, args, what):
        rows = self.db.execute(sql, args).fetchall()
        if len(rows) != 1:
            raise Abort(f"{what}: expected exactly 1 row, found {len(rows)}")
        return rows[0]

    def unit(self, unit_number):
        return self.one("SELECT * FROM equipment WHERE unit_number = ?", (unit_number,), f"unit {unit_number}")["id"]

    def category(self, name):
        return self.one("SELECT id FROM catalog_categories WHERE name = ?", (name,), f"category {name}")["id"]

    def item(self, value, category):
        return self.one(
            """SELECT catalog_items.id FROM catalog_items
               JOIN catalog_categories ON catalog_categories.id = catalog_items.category_id
               WHERE UPPER(catalog_items.value) = UPPER(?) AND catalog_categories.name = ?""",
            (value, category), f"{category} item {value}",
        )["id"]

    def item_absent(self, value):
        n = self.db.execute("SELECT COUNT(*) FROM catalog_items WHERE UPPER(value) = UPPER(?)", (value,)).fetchone()[0]
        if n:
            raise Abort(f"catalog item {value} already exists -- was this already applied?")

    def unit_absent(self, unit_number):
        if self.db.execute("SELECT 1 FROM equipment WHERE unit_number = ?", (unit_number,)).fetchone():
            raise Abort(f"unit {unit_number} already exists -- was this already applied?")

    # ---- catalog -------------------------------------------------------
    def create_item(self, category, value, alts=()):
        self.item_absent(value)
        item_id = self.db.execute(
            "INSERT INTO catalog_items (category_id, label, value) VALUES (?, ?, ?)",
            (self.category(category), LABELS[category.lower()], value),
        ).lastrowid
        self.say(f"created {category} item {value}")
        for brand, alt in alts:
            self.add_alt(item_id, value, alt, brand)
        return item_id

    def add_alt(self, item_id, item_value, alt, brand):
        exists = self.db.execute(
            "SELECT 1 FROM catalog_item_alternates WHERE catalog_item_id = ? AND UPPER(value) = UPPER(?)",
            (item_id, alt),
        ).fetchone()
        if exists:
            raise Abort(f"{item_value} already has alternate {alt} -- was this already applied?")
        self.db.execute(
            "INSERT INTO catalog_item_alternates (catalog_item_id, value, notes) VALUES (?, ?, ?)",
            (item_id, alt, brand),
        )
        self.say(f"  {item_value}: + {brand} {alt}")

    def delete_alt(self, item_id, item_value, alt, brand):
        cur = self.db.execute(
            "DELETE FROM catalog_item_alternates WHERE catalog_item_id = ? AND value = ? AND notes = ?",
            (item_id, alt, brand),
        )
        if cur.rowcount != 1:
            raise Abort(f"{item_value}: expected to delete 1 alternate {brand} {alt!r}, matched {cur.rowcount}")
        self.say(f"{item_value}: - {brand} {alt!r}")

    def rename_item(self, item_id, old, new, category=None, label=None):
        row = self.one("SELECT value FROM catalog_items WHERE id = ?", (item_id,), f"item {old}")
        if row["value"] != old:
            raise Abort(f"item #{item_id}: expected value {old}, found {row['value']}")
        if new != old:
            self.item_absent(new)
        sets, args = ["value = ?"], [new]
        if category:
            sets.append("category_id = ?"); args.append(self.category(category))
        if label:
            sets.append("label = ?"); args.append(label)
        self.db.execute(f"UPDATE catalog_items SET {', '.join(sets)} WHERE id = ?", args + [item_id])
        extra = (f", category -> {category}" if category else "") + (f", label -> {label!r}" if label else "")
        self.say(f"item {old} -> {new}{extra}" if new != old else f"item {old}{extra}")

    def delete_specs(self, item_id, item_value, where, args, expect):
        cur = self.db.execute(f"DELETE FROM catalog_item_specs WHERE catalog_item_id = ? AND {where}", (item_id, *args))
        if cur.rowcount != expect:
            raise Abort(f"{item_value}: expected to delete {expect} spec(s) ({where}), matched {cur.rowcount}")
        self.say(f"{item_value}: removed {cur.rowcount} spec(s)")

    def merge_item(self, keep_id, dup_id, value, link_quantity=None):
        """Move every unit link from dup onto keep, then delete dup (its
        alternates/specs/history cascade -- they're copies of keep's)."""
        units = self.db.execute(
            """SELECT equipment.unit_number, equipment_catalog_items.*
               FROM equipment_catalog_items JOIN equipment ON equipment.id = equipment_catalog_items.equipment_id
               WHERE catalog_item_id = ? ORDER BY unit_number""",
            (dup_id,),
        ).fetchall()
        for u in units:
            qty = link_quantity if link_quantity is not None else u["quantity"]
            self.db.execute(
                """INSERT OR IGNORE INTO equipment_catalog_items
                   (equipment_id, catalog_item_id, quantity, quantity_unit) VALUES (?, ?, ?, ?)""",
                (u["equipment_id"], keep_id, qty, u["quantity_unit"]),
            )
        self.db.execute("DELETE FROM catalog_items WHERE id = ?", (dup_id,))
        qty_note = f" (quantity {link_quantity})" if link_quantity else ""
        self.say(f"merged duplicate {value} #{dup_id} into #{keep_id}: moved {', '.join(u['unit_number'] for u in units)}{qty_note}")

    # ---- equipment -----------------------------------------------------
    def link(self, unit_number, item_id, item_value):
        cur = self.db.execute(
            "INSERT OR IGNORE INTO equipment_catalog_items (equipment_id, catalog_item_id) VALUES (?, ?)",
            (self.unit(unit_number), item_id),
        )
        if not cur.rowcount:
            raise Abort(f"{unit_number} is already linked to {item_value} -- was this already applied?")
        self.say(f"linked {unit_number} -> {item_value}")

    def add_info(self, unit_number, value, label="OEM part number"):
        equipment_id = self.unit(unit_number)
        if self.db.execute(
            "SELECT 1 FROM equipment_info_items WHERE equipment_id = ? AND label = ? AND value = ?",
            (equipment_id, label, value),
        ).fetchone():
            raise Abort(f"{unit_number} already has {label} {value!r} -- was this already applied?")
        self.db.execute(
            "INSERT INTO equipment_info_items (equipment_id, label, value) VALUES (?, ?, ?)",
            (equipment_id, label, value),
        )
        self.say(f"{unit_number}: + {label}: {value}")

    def set_status(self, unit_number, status, expect_from):
        row = self.one("SELECT status FROM equipment WHERE unit_number = ?", (unit_number,), f"unit {unit_number}")
        if row["status"] not in expect_from:
            raise Abort(f"{unit_number}: expected status in {expect_from}, found {row['status']}")
        self.db.execute(
            "UPDATE equipment SET status = ?, updated_at = datetime('now') WHERE unit_number = ?",
            (status, unit_number),
        )
        self.say(f"{unit_number}: status {row['status']} -> {status}")

    def enable_field(self, equipment_id, field):
        self.db.execute(
            "INSERT OR IGNORE INTO equipment_enabled_fields (equipment_id, field_name) VALUES (?, ?)",
            (equipment_id, field),
        )

    def create_unit(self, unit_number, **fields):
        self.unit_absent(unit_number)
        optional = {k: fields.pop(k) for k in ("year", "engine_size") if k in fields}
        cols = ["unit_number", *fields, *optional]
        equipment_id = self.db.execute(
            f"INSERT INTO equipment ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (unit_number, *fields.values(), *optional.values()),
        ).lastrowid
        for field in optional:
            self.enable_field(equipment_id, field)
        self.say(f"created unit {unit_number}: {fields} {optional}")
        return equipment_id


def run(f):
    print("Section 1: unit status / new units")
    for u in ("014", "081", "096", "183", "807"):
        f.set_status(u, "retired", ("active",))
    f.set_status("024", "retired", ("out_of_service",))

    f.create_unit("064", equipment_type="Automotive Equipment", make="Chevrolet", model="1500",
                  meter_type="kilometers", status="retired", year=2007, engine_size="4.8L")
    f.link("064", f.item("CA8755", "Air"), "CA8755")
    f.link("064", f.item("PH10060", "Oil"), "PH10060")

    # Year/model weren't on the spreadsheet -- fill them in from the app afterwards.
    f.create_unit("100", equipment_type="Heavy Equipment", make="CAT", model="",
                  meter_type="kilometers", status="active")
    for value, category in (("462-1171", "Oil"), ("509-5694", "Fuel"), ("523-4987", "Fuel")):
        f.link("100", f.item(value, category), value)
    f.link("100", f.create_item("Air", "526-3121", (("Donaldson", "P629469"), ("Fleetguard", "AF26121"),
                                                    ("Baldwin", "RS5750"), ("NAPA", "9036"))), "526-3121")

    print("Section 2: filter disagreements (080 fuel links are on hold)")
    # 080 and 084 take different oil filters (checked on the machines).
    ph3950 = f.item("PH3950", "Oil")
    f.delete_alt(ph3950, "PH3950", "P550935", "Donaldson")
    f.add_alt(ph3950, "PH3950", "P550162", "Donaldson")
    jd_oil = f.item("6657635", "Oil")  # old Bobcat number: kept as an alternate, not the main P/N
    f.rename_item(jd_oil, "6657635", "MIU800650")
    f.add_alt(jd_oil, "MIU800650", "6657635", "Bobcat")
    f.delete_alt(jd_oil, "MIU800650", "FS3593A", "Fram")
    f.add_alt(jd_oil, "MIU800650", "PH3593A", "Fram")
    f.add_alt(f.item("MIU802421", "Fuel"), "MIU802421", "129A00-55730", "Yanmar")
    rs3942 = f.create_item("Air", "RS3942", (("Donaldson", "P778984"), ("Takeuchi", "1911112001")))
    rs3943 = f.create_item("Air Inner", "RS3943", (("Takeuchi", "1911112002"),))
    f.link("080", rs3942, "RS3942")
    f.link("080", rs3943, "RS3943")

    secondary = f.item("DZ115390", "Fuel")
    f.rename_item(secondary, "DZ115390", "RE551508")
    f.add_alt(secondary, "RE551508", "DZ115390", "John Deere")
    f.add_alt(secondary, "RE551508", "BF46157", "Baldwin")
    primary = f.create_item("Fuel", "RE551507", (("John Deere", "DZ115391"), ("Donaldson", "P584000"),
                                                 ("Baldwin", "BF46156")))
    f.link("085", primary, "RE551507")

    f.rename_item(f.item("VOE14500233", "Hydraulic"), "VOE14500233", "VOE14500233",
                  category="Breather", label="Breather filter")
    f.rename_item(f.item("VOE15035179", "Hydraulic"), "VOE15035179", "VOE15035179",
                  category="Transmission", label="Transmission filter")
    f.rename_item(f.item("VOE14385476", "Hydraulic"), "VOE14385476", "VOE14385476", label="Hydraulic filter")

    print("Section 3: units the DB had barely loaded")
    pa10136 = f.item("PA10136", "Cabin")
    ram_parts = [
        (f.create_item("Oil", "68677810AA", (("Fleetguard", "LF16453"),)), "68677810AA"),
        (f.create_item("Fuel", "68677800AA", (("Fleetguard", "FK11008NN"),)), "68677800AA"),
        (f.create_item("Air", "PA4318", (("Fleetguard", "AF27684"),)), "PA4318"),
        (pa10136, "PA10136"),
    ]
    for u in ("119", "120"):
        for item_id, value in ram_parts:
            f.link(u, item_id, value)
        f.add_info(u, "53034051AB (Mopar)")
        f.add_info(u, "68318365AA (Mopar)")

    ca10281 = f.item("CA10281", "Air")
    f.add_alt(ca10281, "CA10281", "A0334241000", "Detroit")
    f.link("169", ca10281, "CA10281")
    for value, category, fleetguard in (
        ("A9361807410", "Oil", "LF17811"),
        ("A9360900651", "Fuel", "FK13850"),
        ("A0000904851", "Fuel", "FS20081"),
    ):
        f.link("169", f.create_item(category, value, (("Fleetguard", fleetguard),)), value)
    row = f.one("SELECT id, notes FROM equipment WHERE unit_number = '169'", (), "unit 169")
    if row["notes"] != "We don't have this thing yet.":
        raise Abort(f"169: unexpected notes {row['notes']!r}")
    # Same as removing the Notes field in the app: clears the value too.
    f.db.execute("UPDATE equipment SET notes = NULL, engine_size = '7.7L', updated_at = datetime('now') WHERE id = ?", (row["id"],))
    f.db.execute("DELETE FROM equipment_enabled_fields WHERE equipment_id = ? AND field_name = 'notes'", (row["id"],))
    f.enable_field(row["id"], "engine_size")
    f.say("169: notes cleared, engine size 7.7L")

    f.link("805", f.create_item("Hydraulic", "P3613", (("Luber-finer", "LFH4223"),)), "P3613")
    f.link("187", f.create_item("Hydraulic", "PT23616-MPG"), "PT23616-MPG")

    print("Section 4: duplicate catalog items")
    for value, quantity in (("CA7139", None), ("CA8737", 2), ("CA9550", None)):
        ids = [r["id"] for r in f.db.execute(
            """SELECT catalog_items.id FROM catalog_items
               JOIN catalog_categories ON catalog_categories.id = catalog_items.category_id
               WHERE catalog_items.value = ? AND catalog_categories.name = 'Air' ORDER BY catalog_items.id""",
            (value,),
        )]
        if len(ids) != 2:
            raise Abort(f"{value}: expected 2 duplicate Air items, found {len(ids)}")
        if value == "CA8737":  # the only kept copy carrying a "likely a duplicate" note
            f.delete_specs(ids[0], value, "label = 'Note' AND value LIKE '%likely a duplicate%'", (), 1)
            f.delete_specs(ids[0], value, "label = 'Baldwin cross-ref'", (), 1)
        f.merge_item(ids[0], ids[1], value, link_quantity=quantity)
    f.add_info("027", "A Engine (Nissan UD3300 7.7L): CA7139, CA7139SY, RK444Z5002, BF209Z5008. "
                      "B Engine (JD Powertech 4.5L): CA10790, CA10790SY, RE541922, RE504836",
               label="Maintenance info")

    cabin = f.item("518-9395", "Cabin")
    f.delete_specs(cabin, "518-9395 (Cabin)", "label = 'Note' AND value LIKE 'Shares part number%'", (), 1)
    air_inner = f.item("518-9395", "Air Inner")
    f.db.execute("UPDATE catalog_items SET value = '526-3122' WHERE id = ?", (air_inner,))
    f.say("082 air inner: 518-9395 -> 526-3122")
    f.delete_specs(air_inner, "526-3122", "1 = 1", (), 5)  # the cabin filter's copied dimensions/type
    hydraulic = f.item("538-6396", "Hydraulic")
    f.db.execute("UPDATE catalog_items SET value = '519-9926' WHERE id = ?", (hydraulic,))
    f.say("082 hydraulic: 538-6396 -> 519-9926")
    f.delete_specs(hydraulic, "519-9926", "label = 'Note'", (), 1)

    print("Section 5: cross-reference conflicts")
    f.delete_alt(f.item("HPH6349", "Oil"), "HPH6349", "BD103-SS-33", "Baldwin")
    f.add_alt(f.item("PH10575", "Oil"), "PH10575", "100255", "NAPA")
    g2 = f.item("G2", "Fuel")
    f.delete_alt(g2, "G2", "or BF7725 is also okay", "Other")
    cur = f.db.execute(
        """UPDATE catalog_item_alternates SET value = '12581-43012', notes = 'Kubota (5/16 inline)'
           WHERE catalog_item_id = ? AND value = 'Kubota 12581-43012 5/16" inline'""",
        (g2,),
    )
    if cur.rowcount != 1:
        raise Abort("G2: Kubota alternate not found as expected")
    f.say("G2: Kubota alternate -> 12581-43012 (Kubota (5/16 inline))")

    print("Section 6: OEM part numbers")
    for u, values in (
        ("016", ["FL-500S (Ford)"]),
        ("017", ["FL-500S (Ford)"]),
        ("020", ["68190705AB (Mopar)", "68318365AA (Mopar)", "68191349AC/MO-349 (Mopar)"]),
        ("029", ["24-789-01-S (Kohler)"]),
        ("144", ["0E9371A"]),
        ("146", ["071751A"]),
        ("147", ["871751A"]),
        ("206", ["PL125 (Racor)", "21.0001", "21.0078"]),
        ("207", ["21.0122 (Ventrac)"]),
        ("208", ["21.0122 (Ventrac)"]),
        ("209", ["21.0122 (Ventrac)"]),
        ("807", ["29526898 (Allison)"]),
        ("361", ["68191349AC/MO-349 (Mopar)"]),  # same as 003/012/020, which use the same filter
    ):
        for value in values:
            f.add_info(u, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database")
    parser.add_argument("--apply", action="store_true", help="commit the changes (default: dry run)")
    args = parser.parse_args()

    db = sqlite3.connect(args.database, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("ABORT: database failed integrity_check")

    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = f"{args.database}.before-reconcile-{stamp}"
        with sqlite3.connect(backup_path) as dest:
            db.backup(dest)
        print(f"Backed up to {backup_path}")

    db.execute("BEGIN IMMEDIATE")
    f = Fleet(db)
    try:
        run(f)
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise Abort("foreign_key_check found dangling references")
    except Abort as e:
        db.execute("ROLLBACK")
        sys.exit(f"ABORT: {e}\nNothing was changed.")
    except Exception:
        db.execute("ROLLBACK")
        raise

    if args.apply:
        db.execute("COMMIT")
        print(f"\nApplied {len(f.log)} changes.")
    else:
        db.execute("ROLLBACK")
        print(f"\nDry run OK: {len(f.log)} changes would be made. Nothing was written; rerun with --apply.")


if __name__ == "__main__":
    main()
