import sqlite3

from app import create_app
from app.db import get_db
from app.routers.suppliers import tel_href, website_href

from .conftest import login


def _add_supplier(client, **overrides):
    data = {
        "name": "Bytown Diesel",
        "phone": "(613) 555-0142 ext 12",
        "email": "parts@bytown.example",
        "website": "bytown.example",
        "account_number": "ACCT-77",
        "brands": "Baldwin, Donaldson, baldwin",
    }
    data.update(overrides)
    resp = client.post("/suppliers/new", data=data)
    assert resp.status_code == 302, resp.data
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def _category_id(app, name):
    with app.app_context():
        return get_db().execute("SELECT id FROM supplier_categories WHERE name = ?", (name,)).fetchone()["id"]


def test_tel_and_website_hrefs():
    assert tel_href("(613) 555-0142 ext 12") == "tel:6135550142"
    assert tel_href("+1 613.555.0142") == "tel:+16135550142"
    assert tel_href("613-555-0142 x4") == "tel:6135550142"
    assert tel_href("call the shop") is None
    assert website_href("baldwin.com") == "https://baldwin.com"
    assert website_href("http://x.example") == "http://x.example"
    assert website_href("javascript:alert(1)") == "https://javascript:alert(1)"


def test_migration_seeds_categories_and_leaves_existing_data(tmp_path):
    path = str(tmp_path / "old.sqlite")
    # An "existing" database from before suppliers existed, with real data in it.
    app = create_app({"TESTING": True, "DATABASE": path})
    with app.app_context():
        db = get_db()
        db.execute("DROP TABLE supplier_brands")
        db.execute("DROP TABLE supplier_contacts")
        db.execute("DROP TABLE supplier_category_links")
        db.execute("DROP TABLE supplier_categories")
        db.execute("DROP TABLE suppliers")
        db.execute("DELETE FROM schema_migrations WHERE name IN ('0004_seed_supplier_categories', '0005_grant_suppliers_edit')")
        db.execute("DELETE FROM role_permissions WHERE permission = 'suppliers.edit'")
        db.execute(
            "INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('T-1', 'Truck', 'Ford', 'F-550')"
        )
        db.commit()

    # Restarting runs init_db() again, which should add the supplier tables.
    app = create_app({"TESTING": True, "DATABASE": path})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT unit_number FROM equipment").fetchall()[0]["unit_number"] == "T-1"
        names = [r["name"] for r in db.execute("SELECT name FROM supplier_categories ORDER BY sort_order")]
        assert names[:3] == ["Parts", "Filters", "Tires"] and len(names) == 10
        assert db.execute(
            "SELECT 1 FROM role_permissions WHERE role = 'editor' AND permission = 'suppliers.edit'"
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM role_permissions WHERE role = 'viewer' AND permission = 'suppliers.edit'"
        ).fetchone()
        # A deleted default category must not come back on the next start.
        db.execute("DELETE FROM supplier_categories WHERE name = 'Glass'")
        db.commit()

    app = create_app({"TESTING": True, "DATABASE": path})
    with app.app_context():
        assert not get_db().execute("SELECT 1 FROM supplier_categories WHERE name = 'Glass'").fetchone()


def test_requires_login(client):
    resp = client.get("/suppliers/")
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


def test_viewer_can_read_but_not_edit(app, client):
    login(client, "editor")
    supplier_id = _add_supplier(client)
    client.get("/auth/logout")

    login(client, "viewer")
    assert client.get("/suppliers/").status_code == 200
    assert client.get(f"/suppliers/{supplier_id}").status_code == 200
    assert client.get("/suppliers/new").status_code == 403
    assert client.post(f"/suppliers/{supplier_id}/deactivate").status_code == 403
    assert client.post("/suppliers/categories/new", data={"name": "Electrical"}).status_code == 403
    assert b"+ Add supplier" not in client.get("/suppliers/").data


def test_create_and_detail_links(app, client):
    login(client, "editor")
    filters = _category_id(app, "Filters")
    supplier_id = _add_supplier(client, category_ids=[str(filters)])

    with app.app_context():
        db = get_db()
        s = db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        assert s["created_by"] == "editor" and s["updated_by"] == "editor" and s["is_active"] == 1
        brands = [r["brand"] for r in db.execute("SELECT brand FROM supplier_brands ORDER BY brand")]
        assert brands == ["Baldwin", "Donaldson"]

    page = client.get(f"/suppliers/{supplier_id}").get_data(as_text=True)
    assert 'href="tel:6135550142"' in page
    assert 'href="mailto:parts@bytown.example"' in page
    assert 'href="https://bytown.example"' in page
    assert "Filters" in page and "ACCT-77" in page


def test_name_required_and_email_checked(client):
    login(client, "editor")
    resp = client.post("/suppliers/new", data={"name": "  "})
    assert resp.status_code == 200 and b"Company name is required" in resp.data
    resp = client.post("/suppliers/new", data={"name": "X", "email": "nope"})
    assert b"doesn&#39;t look right" in resp.data


def test_contacts_and_last_edited(app, client):
    login(client, "editor")
    supplier_id = _add_supplier(client)
    client.get("/auth/logout")
    login(client, "admin")

    resp = client.post(f"/suppliers/{supplier_id}/contacts/new", data={
        "name": "Dave O'Brien", "role": "Parts counter", "cell_phone": "613 555 9999", "email": "dave@bytown.example",
    })
    assert resp.status_code == 302
    page = client.get(f"/suppliers/{supplier_id}").get_data(as_text=True)
    assert "Dave O&#39;Brien" in page and 'href="tel:6135559999"' in page and "by admin" in page

    with app.app_context():
        db = get_db()
        contact_id = db.execute("SELECT id FROM supplier_contacts").fetchone()["id"]
        assert db.execute("SELECT updated_by FROM suppliers").fetchone()["updated_by"] == "admin"

    client.post(f"/suppliers/{supplier_id}/contacts/{contact_id}/edit", data={"name": "Dave", "role": "bogus"})
    with app.app_context():
        row = get_db().execute("SELECT name, role FROM supplier_contacts").fetchone()
        assert row["name"] == "Dave" and row["role"] is None

    # A contact id that belongs to another supplier is a 404, not an edit.
    other_id = _add_supplier(client, name="Other")
    assert client.post(f"/suppliers/{other_id}/contacts/{contact_id}/delete").status_code == 404

    client.post(f"/suppliers/{supplier_id}/contacts/{contact_id}/delete")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM supplier_contacts").fetchone()["n"] == 0


def test_list_search_filter_sort_and_deactivate(app, client):
    login(client, "editor")
    tires = _category_id(app, "Tires")
    a = _add_supplier(client, name="zed Tire", brands="Michelin", category_ids=[str(tires)])
    _add_supplier(client, name="Acme Filters", brands="Fleetguard")
    c = _add_supplier(client, name="middle Glass", brands="")
    client.post(f"/suppliers/{c}/contacts/new", data={"name": "Priya Shah"})

    def names(**params):
        page = client.get("/suppliers/", query_string=params).get_data(as_text=True)
        # Only the list itself -- pending "Added ..." flash messages sit above it.
        page = page[page.find('class="panel"'):]
        return [n for n in ("Acme Filters", "middle Glass", "zed Tire") if n in page], page

    found, page = names()
    assert found == ["Acme Filters", "middle Glass", "zed Tire"]
    assert page.index("Acme Filters") < page.index("middle Glass") < page.index("zed Tire")

    assert names(q="fleetguard")[0] == ["Acme Filters"]        # brand
    assert names(q="priya")[0] == ["middle Glass"]             # contact name
    assert names(q="tires")[0] == ["zed Tire"]                 # category name
    assert names(q="acme")[0] == ["Acme Filters"]              # company
    assert names(category=tires)[0] == ["zed Tire"]

    client.post(f"/suppliers/{a}/deactivate")
    assert names()[0] == ["Acme Filters", "middle Glass"]
    assert names(show="inactive")[0] == ["zed Tire"]
    assert names(show="all")[0] == ["Acme Filters", "middle Glass", "zed Tire"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"] == 3

    client.post(f"/suppliers/{a}/reactivate")
    assert "zed Tire" in names()[0]


def test_edit_replaces_categories_and_brands(app, client):
    login(client, "editor")
    parts, fuel = _category_id(app, "Parts"), _category_id(app, "Fuel")
    supplier_id = _add_supplier(client, category_ids=[str(parts)])
    resp = client.post(f"/suppliers/{supplier_id}/edit", data={
        "name": "Bytown Diesel Ltd", "category_ids": [str(fuel)], "brands": "Wix",
    })
    assert resp.status_code == 302
    with app.app_context():
        db = get_db()
        cats = [r["category_id"] for r in db.execute("SELECT category_id FROM supplier_category_links")]
        assert cats == [fuel]
        assert [r["brand"] for r in db.execute("SELECT brand FROM supplier_brands")] == ["Wix"]
        assert db.execute("SELECT name, phone FROM suppliers").fetchone()["name"] == "Bytown Diesel Ltd"


def test_category_list_editing(app, client):
    login(client, "editor")
    filters = _category_id(app, "Filters")
    supplier_id = _add_supplier(client, category_ids=[str(filters)])

    client.post("/suppliers/categories/new", data={"name": "Electrical"})
    resp = client.post("/suppliers/categories/new", data={"name": "electrical"})
    assert resp.status_code == 302
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) AS n FROM supplier_categories WHERE name = 'Electrical' COLLATE NOCASE"
        ).fetchone()["n"] == 1

    client.post(f"/suppliers/categories/{filters}/edit", data={"name": "Filtration"})
    assert b"Filtration" in client.get(f"/suppliers/{supplier_id}").data

    client.post(f"/suppliers/categories/{filters}/delete")
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) AS n FROM supplier_category_links").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"] == 1


def test_nav_has_suppliers_link(client):
    login(client, "viewer")
    assert b'href="/suppliers/"' in client.get("/").data
