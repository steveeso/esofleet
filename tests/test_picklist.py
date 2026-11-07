from app.db import get_db

from .conftest import login


def _make_parts(app):
    """Three parts: two counted (one on a warehouse shelf), one never counted."""
    with app.app_context():
        db = get_db()
        cat = db.execute("INSERT INTO catalog_categories (name) VALUES ('Oil')").lastrowid
        ids = {}
        for value, location, code, on_hand in (
            ("BT223", "Aisle 07, Bank B, Shelf 4", "gm_down-07-B-4", 5),
            ("P550", "Aisle 02, Bank A, Shelf 1", "gm_down-02-A-1", 1),
            ("LF3000", None, None, None),
        ):
            ids[value] = db.execute(
                """INSERT INTO catalog_items (category_id, label, value, location, location_code, quantity_on_hand)
                   VALUES (?, 'Oil filter', ?, ?, ?, ?)""",
                (cat, value, location, code, on_hand),
            ).lastrowid
        unit = db.execute(
            "INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('T-1', 'Truck', 'Ford', 'F-550')"
        ).lastrowid
        db.execute("INSERT INTO equipment_catalog_items (equipment_id, catalog_item_id, quantity) VALUES (?, ?, 2)",
                   (unit, ids["BT223"]))
        db.execute("""INSERT INTO equipment_catalog_items (equipment_id, catalog_item_id, quantity, quantity_unit)
                      VALUES (?, ?, 5.7, 'liters')""", (unit, ids["P550"]))
        db.commit()
    return ids, unit


def _list(app, username):
    with app.app_context():
        rows = get_db().execute(
            """SELECT c.value, p.quantity FROM pick_list_items p
               JOIN catalog_items c ON c.id = p.catalog_item_id
               JOIN users u ON u.id = p.user_id WHERE u.username = ?""",
            (username,),
        ).fetchall()
        return {r["value"]: r["quantity"] for r in rows}


def _on_hand(app):
    with app.app_context():
        return {r["value"]: r["quantity_on_hand"] for r in get_db().execute("SELECT value, quantity_on_hand FROM catalog_items")}


def test_add_accumulates_and_lists_are_per_user(app, client):
    ids, _ = _make_parts(app)
    login(client, "viewer")
    client.post("/pick-list/add", data={"item_id": ids["BT223"]})
    resp = client.post("/pick-list/add", data={"item_id": ids["BT223"], "quantity": "3"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.get_json() == {"ok": True, "count": 1}
    assert _list(app, "viewer") == {"BT223": 4}
    assert _list(app, "editor") == {}
    assert client.post("/pick-list/add", data={"item_id": "99999"}).status_code == 404


def test_page_shows_part_location_qty_in_shelf_order(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    for value in ("LF3000", "BT223", "P550"):
        client.post("/pick-list/add", data={"item_id": ids[value]})
    page = client.get("/pick-list/").get_data(as_text=True)
    page = page[page.find("pick-table"):]
    assert page.index("P550") < page.index("BT223") < page.index("LF3000")
    assert "Aisle 07, Bank B, Shelf 4" in page
    assert 'class="nav-badge nav-pick-count"' in client.get("/").get_data(as_text=True)


def test_add_unit_uses_link_quantities(app, client):
    ids, unit = _make_parts(app)
    login(client, "editor")
    client.post(f"/pick-list/add-unit/{unit}", data={"next": f"/equipment/{unit}"})
    # 2 filters; 5.7 liters of oil isn't a part count, so it goes on as 1.
    assert _list(app, "editor") == {"BT223": 2, "P550": 1}


def test_set_quantity_and_remove(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"]})
    resp = client.post(f"/pick-list/items/{ids['BT223']}/quantity", data={"quantity": "0"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.get_json()["quantity"] == 1   # never below 1
    client.post(f"/pick-list/items/{ids['BT223']}/quantity", data={"quantity": "6"})
    assert _list(app, "editor") == {"BT223": 6}
    client.post(f"/pick-list/items/{ids['BT223']}/remove")
    assert _list(app, "editor") == {}


def test_clear_without_removing_from_inventory(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"], "quantity": "2"})
    client.post("/pick-list/clear")
    assert _list(app, "editor") == {}
    assert _on_hand(app)["BT223"] == 5


def test_subtract_selected_from_inventory(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"], "quantity": "2"})
    client.post("/pick-list/add", data={"item_id": ids["P550"], "quantity": "3"})   # only 1 on hand
    client.post("/pick-list/add", data={"item_id": ids["LF3000"]})                  # never counted
    resp = client.post("/pick-list/subtract",
                       data={"item_id": [ids["BT223"], ids["P550"], ids["LF3000"]]}, follow_redirects=True)
    assert _on_hand(app) == {"BT223": 3, "P550": 0, "LF3000": None}
    assert _list(app, "editor") == {}
    text = resp.get_data(as_text=True)
    assert "worth a recount" in text and "LF3000" in text


def test_subtract_leaves_unticked_parts_alone(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"], "quantity": "2"})
    client.post("/pick-list/add", data={"item_id": ids["P550"]})
    client.post("/pick-list/subtract", data={"item_id": [ids["BT223"]]})
    assert _on_hand(app)["BT223"] == 3 and _on_hand(app)["P550"] == 1
    assert _list(app, "editor") == {"P550": 1}   # unticked part stays on the list
    # Nothing ticked: nothing changes.
    resp = client.post("/pick-list/subtract", data={}, follow_redirects=True)
    assert b"Tick at least one part" in resp.data
    assert _list(app, "editor") == {"P550": 1}


def test_subtract_ignores_parts_not_on_your_list(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"]})
    client.post("/pick-list/subtract", data={"item_id": [ids["P550"]]})   # not on this user's list
    assert _on_hand(app)["P550"] == 1
    assert _list(app, "editor") == {"BT223": 1}


def test_page_has_ticked_checkboxes_and_both_buttons(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    client.post("/pick-list/add", data={"item_id": ids["BT223"]})
    page = client.get("/pick-list/").get_data(as_text=True)
    assert 'class="pick-select"' in page and "checked" in page
    assert "Subtract Selected From Inventory" in page and "Clear List" in page


def test_subtract_needs_permission(app, client):
    ids, _ = _make_parts(app)
    login(client, "viewer")
    client.post("/pick-list/add", data={"item_id": ids["BT223"], "quantity": "2"})
    page = client.get("/pick-list/").data
    assert b'class="pick-select"' not in page and b"Subtract Selected" not in page
    client.post("/pick-list/subtract", data={"item_id": [ids["BT223"]]})
    assert _on_hand(app)["BT223"] == 5
    assert _list(app, "viewer") == {"BT223": 2}   # kept, since nothing was done


def test_next_must_be_local(app, client):
    ids, _ = _make_parts(app)
    login(client, "editor")
    resp = client.post("/pick-list/add", data={"item_id": ids["BT223"], "next": "//evil.example/"})
    assert resp.headers["Location"] == "/pick-list/"
