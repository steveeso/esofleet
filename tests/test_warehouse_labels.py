import copy

from app import db as app_db
from app import warehouse
from app.db import get_db


def test_label_hidden_while_only_one_room_has_shelving():
    # gm_up is registered but empty, so it must not force "GM-Down —" onto every shelf.
    assert len(warehouse.room_ids()) > 1 and warehouse.populated_room_ids() == ["gm_down"]
    assert warehouse.compose("04", "A", 4) == ("Aisle 04, Bank A, Shelf 4", "gm_down-04-A-4")


def test_label_shown_once_a_second_room_has_shelving(monkeypatch):
    up = copy.deepcopy(warehouse.ROOMS["gm_up"])
    up["structures"] = [{"aisles": {"E": "20"}, "x": (0.0, 0.6), "y": (0.0, 5.0), "banks": 2, "dir": "NS"}]
    monkeypatch.setitem(warehouse.ROOMS, "gm_up", up)
    assert warehouse.compose("04", "A", 4)[0] == "GM-Down — Aisle 04, Bank A, Shelf 4"
    assert warehouse.compose("20", "B", 2, "gm_up")[0] == "GM-Up — Aisle 20, Bank B, Shelf 2"


def test_startup_normalizes_saved_location_text(app):
    with app.app_context():
        db = get_db()
        cat = db.execute("INSERT INTO catalog_categories (name) VALUES ('Filters')").lastrowid
        ids = {}
        for key, location, code in (
            ("prefixed", "GM-Down — Aisle 04, Bank A, Shelf 4", "gm_down-04-A-4"),
            ("bare", "Aisle 06, Bank C, Shelf 5", "gm_down-06-C-5"),
            ("freetext", "Dad's garage", None),
            ("stale_code", "GM-Down — Aisle 99, Bank A, Shelf 1", "gm_down-99-A-1"),
        ):
            ids[key] = db.execute(
                "INSERT INTO catalog_items (category_id, label, value, location, location_code) VALUES (?, 'F', ?, ?, ?)",
                (cat, key, location, code),
            ).lastrowid
        db.commit()
        history_before = db.execute("SELECT COUNT(*) AS n FROM catalog_item_location_history").fetchone()["n"]

        app_db.init_db()
        app_db.init_db()   # safe to repeat

        loc = {r["value"]: r["location"] for r in db.execute("SELECT value, location FROM catalog_items")}
        assert loc["prefixed"] == "Aisle 04, Bank A, Shelf 4"
        assert loc["bare"] == "Aisle 06, Bank C, Shelf 5"
        assert loc["freetext"] == "Dad's garage"
        assert loc["stale_code"] == "GM-Down — Aisle 99, Bank A, Shelf 1"   # code no longer on the floor plan: untouched
        assert db.execute("SELECT COUNT(*) AS n FROM catalog_item_location_history").fetchone()["n"] == history_before
