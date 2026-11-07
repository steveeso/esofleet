import importlib.util
import io
from pathlib import Path

import openpyxl

from app.db import get_db
from app.routers import catalog

HEADERS = ["Unit", "Main P/N", "Type", "OEM Number", "OEM Brand", "Fram", "Donaldson", "Fleetguard", "Baldwin", "NAPA", "Other", "Notes"]


def _load_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "oem_to_alternates.py"
    spec = importlib.util.spec_from_file_location("oem_to_alternates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Active List"
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _alternates(db, part_value):
    return {(r["value"], r["notes"]) for r in db.execute(
        """SELECT a.value, a.notes FROM catalog_item_alternates a JOIN catalog_items c ON c.id = a.catalog_item_id
           WHERE c.value = ?""", (part_value,))}


def test_import_files_oem_number_as_an_alternate(app):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('001', 'Truck', 'Ford', 'F-650')")
        db.execute("INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('002', 'Truck', 'Ford', 'F-650')")
        db.commit()
        data = _workbook([
            ["001", "PH10890", "Oil", "FL2124S", "Ford", None, "P502503", None, "B7379", None, None, None],
            ["002", "CA10888", "Air", "FA2061", None, None, None, None, None, None, None, None],   # no brand given
            ["002", "PA4486", "Cabin", "PA4486", "Ford", None, None, None, None, None, None, None],  # same as primary
        ])
        plan = catalog._parse_filter_workbook(data, db)
        assert "oem_infos" not in plan
        catalog._commit_filter_plan(plan, db)

        assert _alternates(db, "PH10890") == {("P502503", "Donaldson"), ("B7379", "Baldwin"), ("FL2124S", "Ford")}
        assert _alternates(db, "CA10888") == {("FA2061", "OEM")}
        assert _alternates(db, "PA4486") == set()          # repeating the primary number adds nothing
        assert db.execute("SELECT COUNT(*) AS n FROM equipment_info_items WHERE label = 'OEM part number'").fetchone()["n"] == 0


def test_script_moves_matched_oem_info_items_and_leaves_the_rest(app):
    script = _load_script()
    with app.app_context():
        db = get_db()
        cat = db.execute("INSERT INTO catalog_categories (name) VALUES ('Filters')").lastrowid
        parts = {}
        for value in ("PH10890", "CA10888"):
            parts[value] = db.execute(
                "INSERT INTO catalog_items (category_id, label, value) VALUES (?, 'F', ?)", (cat, value)).lastrowid
        units = {}
        for number in ("001", "002", "003"):
            units[number] = db.execute(
                "INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES (?, 'Truck', 'Ford', 'F')",
                (number,)).lastrowid
        for number in ("001", "002"):   # 003 isn't linked to anything
            for value in ("PH10890", "CA10888"):
                db.execute("INSERT INTO equipment_catalog_items (equipment_id, catalog_item_id) VALUES (?, ?)",
                           (units[number], parts[value]))
        for number, value in (
            ("001", "FL2124S (Ford)"),   # pairs with PH10890
            ("002", "FL2124S (Ford)"),   # same number on another unit sharing the part: collapses into one alternate
            ("001", "FA2061"),           # no brand -> note "OEM"
            ("001", "ZZ-999 (Ford)"),    # not on the sheet for this unit -> left alone
            ("003", "FL2124S (Ford)"),   # sheet's part isn't linked to unit 003 -> left alone
        ):
            db.execute("INSERT INTO equipment_info_items (equipment_id, label, value) VALUES (?, 'OEM part number', ?)",
                       (units[number], value))
        db.commit()

        sheet = {
            "001": [("PH10890", "FL2124S"), ("CA10888", "FA2061")],
            "002": [("PH10890", "FL2124S")],
            "003": [("PH10890", "FL2124S")],
        }
        log, problems = script.run(db, sheet)

        assert len(log) == 3 and len(problems) == 2
        assert _alternates(db, "PH10890") == {("FL2124S", "Ford")}      # one alternate, not two
        assert _alternates(db, "CA10888") == {("FA2061", "OEM")}
        left = {r["value"] for r in db.execute("SELECT value FROM equipment_info_items WHERE label = 'OEM part number'")}
        assert left == {"ZZ-999 (Ford)", "FL2124S (Ford)"}
        assert {p[1] for p in problems} == {"ZZ-999 (Ford)", "FL2124S (Ford)"}
        assert {p[0] for p in problems} == {"001", "003"}
