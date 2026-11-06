from app.db import get_db

from .conftest import login


def _part(app):
    with app.app_context():
        db = get_db()
        cat = db.execute("INSERT INTO catalog_categories (name) VALUES ('Fuel')").lastrowid
        item = db.execute(
            "INSERT INTO catalog_items (category_id, label, value) VALUES (?, 'Fuel filter', 'BF7633')", (cat,)
        ).lastrowid
        db.commit()
        return item


def test_anyone_can_report_and_sees_only_their_own(app, client):
    item = _part(app)
    login(client, "viewer")
    assert b"BF7633" in client.get(f"/reports/new?item={item}").data
    resp = client.post("/reports/new", data={"item": item, "kind": "reorder", "message": "Last one, order more"})
    assert resp.headers["Location"] == f"/catalog/items/{item}"
    client.get("/auth/logout")

    login(client, "editor")
    client.post("/reports/new", data={"kind": "problem", "message": "Shop printer is out of toner"})
    page = client.get("/reports/").get_data(as_text=True)
    assert "toner" in page and "Last one" not in page
    assert client.post("/reports/1/resolve").status_code == 302   # admin_required redirects away
    with app.app_context():
        assert get_db().execute("SELECT status FROM reports WHERE id = 1").fetchone()["status"] == "open"


def test_message_required(client):
    login(client, "viewer")
    resp = client.post("/reports/new", data={"message": "  "})
    assert resp.status_code == 200 and b"Say what the problem is" in resp.data
    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"] == 0


def test_admin_inbox_badge_resolve_and_reopen(app, client):
    item = _part(app)
    login(client, "viewer")
    client.post("/reports/new", data={"item": item, "kind": "problem", "message": "Wrong part number"})
    client.get("/auth/logout")

    login(client, "admin")
    home = client.get("/").get_data(as_text=True)
    assert 'nav-badge-alert">1<' in home
    assert "Wrong part number" in client.get("/reports/").get_data(as_text=True)

    client.post("/reports/1/resolve", data={"admin_reply": "Fixed, it's a BF7633-D"})
    assert "nav-badge-alert" not in client.get("/").get_data(as_text=True)
    assert "Wrong part number" not in client.get("/reports/").get_data(as_text=True)
    client.get("/auth/logout")

    # The sender sees it resolved, with the reply.
    login(client, "viewer")
    page = client.get("/reports/?status=resolved").get_data(as_text=True)
    assert "Resolved by admin" in page and "BF7633-D" in page
    client.get("/auth/logout")

    login(client, "admin")
    client.post("/reports/1/reopen")
    with app.app_context():
        assert get_db().execute("SELECT status FROM reports WHERE id = 1").fetchone()["status"] == "open"


def test_report_survives_deleting_the_part(app, client):
    item = _part(app)
    login(client, "viewer")
    client.post("/reports/new", data={"item": item, "message": "Gone?"})
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM catalog_items WHERE id = ?", (item,))
        db.commit()
        row = db.execute("SELECT catalog_item_id, message FROM reports").fetchone()
        assert row["catalog_item_id"] is None and row["message"] == "Gone?"
    client.get("/auth/logout")
    login(client, "admin")
    assert client.get("/reports/").status_code == 200
