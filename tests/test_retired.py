import pytest

from app.db import get_db

from .conftest import login


@pytest.fixture
def fleet(app):
    """Two units sharing one oil filter -- 001 in service, 096 retired."""
    with app.app_context():
        db = get_db()
        ids = {}
        for unit, make, status in (("001", "Ford", "active"), ("096", "CAT", "retired")):
            ids[unit] = db.execute(
                """INSERT INTO equipment (unit_number, equipment_type, make, model, status)
                   VALUES (?, 'Trailers', ?, 'X', ?)""",
                (unit, make, status),
            ).lastrowid
        category_id = db.execute("INSERT INTO catalog_categories (name) VALUES ('Oil')").lastrowid
        ids["part"] = db.execute(
            "INSERT INTO catalog_items (category_id, label, value) VALUES (?, 'Oil filter', 'PH2')",
            (category_id,),
        ).lastrowid
        for unit in ("001", "096"):
            db.execute(
                "INSERT INTO equipment_catalog_items (equipment_id, catalog_item_id) VALUES (?, ?)",
                (ids[unit], ids["part"]),
            )
        db.commit()
    return ids


def test_retired_units_are_left_out_of_fleet_views(client, fleet):
    login(client, "viewer")
    for url in ("/", "/equipment/", "/equipment/table"):
        page = client.get(url).get_data(as_text=True)
        assert "Ford" in page and "CAT" not in page, url

    export = client.get("/equipment/export.csv").get_data(as_text=True)
    assert "001" in export and "096" not in export
    export = client.get("/equipment/export.csv?status=retired").get_data(as_text=True)
    assert "096" in export and "001" not in export

    parts_list = client.get("/catalog/parts-list").get_data(as_text=True)
    assert ">001<" in parts_list and ">096<" not in parts_list


def test_retired_page_lists_units_with_their_filters(client, fleet):
    login(client, "viewer")
    page = client.get("/equipment/retired").get_data(as_text=True)
    assert "096" in page and "CAT" in page and "PH2" in page
    assert "Ford" not in page
    assert 'href="/equipment/retired"' in client.get("/").get_data(as_text=True)


def test_retired_unit_pages_still_work(client, fleet):
    login(client, "viewer")
    detail = client.get(f"/equipment/{fleet['096']}").get_data(as_text=True)
    assert "Retired" in detail and "All retired units" in detail

    used_by = client.get(f"/catalog/items/{fleet['part']}").get_data(as_text=True)
    assert "096 (retired)" in used_by and "001" in used_by


def test_a_unit_can_be_retired_from_the_edit_form(app, client, fleet):
    login(client, "editor")
    resp = client.post(f"/equipment/{fleet['001']}/edit", data={
        "unit_number": "001", "equipment_type": "Trailers", "make": "Ford", "model": "X",
        "status": "retired", "meter_type": "kilometers", "meter_reading": "0",
    })
    assert resp.status_code == 302
    with app.app_context():
        status = get_db().execute("SELECT status FROM equipment WHERE id = ?", (fleet["001"],)).fetchone()[0]
    assert status == "retired"
