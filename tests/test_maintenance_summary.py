import re

from app.db import get_db

from .conftest import login


def _unit_001(app):
    with app.app_context():
        db = get_db()
        unit = db.execute(
            "INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('001', 'Truck', 'Ford', 'F-550')"
        ).lastrowid
        filters = db.execute("INSERT INTO catalog_categories (name, sort_order) VALUES ('Filters', 0)").lastrowid
        oils = db.execute("INSERT INTO catalog_categories (name, sort_order) VALUES ('Oil Types', 1)").lastrowid
        for cat, label, value, location, qty, qty_unit in (
            (filters, "Air filter", "CA1088", "Aisle 04, Bank C, Shelf 1", 1, None),
            (filters, "Oil filter", "PH10890", "Aisle 04, Bank A, Shelf 2", None, None),
            (oils, "Engine oil", "15W-40", None, 16.1, "L"),
        ):
            item = db.execute(
                "INSERT INTO catalog_items (category_id, label, value, location) VALUES (?, ?, ?, ?)",
                (cat, label, value, location),
            ).lastrowid
            db.execute(
                """INSERT INTO equipment_catalog_items (equipment_id, catalog_item_id, quantity, quantity_unit)
                   VALUES (?, ?, ?, ?)""",
                (unit, item, qty, qty_unit),
            )
        db.execute(
            """INSERT INTO equipment_info_items (equipment_id, label, value, quantity, quantity_unit)
               VALUES (?, 'Coolant capacity', NULL, 38, 'L')""",
            (unit,),
        )
        db.commit()
        return unit


def _glance_lines(html):
    section = html[html.index('class="panel glance"'):]
    section = section[:section.index("</section>")]
    lines = []
    for li in re.findall(r"<li>(.*?)</li>", section, re.S):
        li = re.sub(r"<button.*?</button>", "", li, flags=re.S)   # the pick list "+" isn't part of the text
        text = re.sub(r"<[^>]+>", "", li)
        lines.append(" ".join(text.split()))
    return lines


def test_maintenance_summary(app, client):
    unit = _unit_001(app)
    login(client, "viewer")
    html = client.get(f"/equipment/{unit}").get_data(as_text=True)
    assert _glance_lines(html) == [
        "Air filter: CA1088 – Aisle 04, Bank C, Shelf 1",
        "Oil filter: PH10890 – Aisle 04, Bank A, Shelf 2",
        "Engine oil: 15W-40 – 16.1 L",
        "Coolant capacity: 38 L",
    ]
    assert ">Maintenance Summary</h2>" in html
    # Sits above the rest of the page.
    assert html.index('class="panel glance"') < html.index('id="equipment-info"')


def test_summary_parts_have_pick_list_buttons(app, client):
    unit = _unit_001(app)
    with app.app_context():
        db = get_db()
        ids = {r["value"]: r["id"] for r in db.execute("SELECT id, value FROM catalog_items")}
        # This unit takes two of the air filter.
        db.execute("UPDATE equipment_catalog_items SET quantity = 2 WHERE catalog_item_id = ?", (ids["CA1088"],))
        db.commit()
    login(client, "viewer")
    html = client.get(f"/equipment/{unit}").get_data(as_text=True)
    section = html[html.index('class="panel glance"'):]
    section = section[:section.index("</section>")]
    buttons = re.findall(r'<button[^>]*class="pick-add-btn" data-item-id="(\d+)" data-quantity="(\d+)"', section)
    assert section.count("grab-icon") == 3   # a hand, not a "+" that looks like creating a record
    # The button comes first on each line, like the Parts List.
    assert all(li.lstrip().startswith(("<button", "<span class=\"glance-pick-spacer\""))
               for li in re.findall(r"<li>(.*?)</li>", section, re.S))
    # One per linked part, none for the per-unit spec (coolant capacity isn't a catalog part).
    assert sorted(buttons) == sorted([
        (str(ids["CA1088"]), "2"),    # a plain count: the quantity this unit takes
        (str(ids["PH10890"]), "1"),   # no quantity recorded: one
        (str(ids["15W-40"]), "1"),    # 16.1 L is a volume, not a part count: one
    ])
    # The button posts to the same endpoint as the Parts List's "+".
    resp = client.post("/pick-list/add", data={"item_id": ids["CA1088"], "quantity": "2"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.get_json() == {"ok": True, "count": 1}


def test_no_summary_without_maintenance_info(app, client):
    with app.app_context():
        db = get_db()
        unit = db.execute(
            "INSERT INTO equipment (unit_number, equipment_type, make, model) VALUES ('002', 'Trailer', 'Big Tex', '14GN')"
        ).lastrowid
        db.commit()
    login(client, "viewer")
    assert 'class="panel glance"' not in client.get(f"/equipment/{unit}").get_data(as_text=True)
