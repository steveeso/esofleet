import base64
import io
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from openpyxl import load_workbook

from ..db import get_db
from .auth import login_required

bp = Blueprint("catalog", __name__, url_prefix="/catalog")

FILTER_IMPORT_SHEET = "Active List"

# Column headers this app recognizes in a filter spreadsheet, lowercased —
# anything past "Notes" (Qty/Year, Fluid, Capacity, etc.) is intentionally
# ignored. Values map to the internal keys used while parsing a row.
FILTER_IMPORT_HEADERS = {
    "unit": "unit", "main p/n": "value", "type": "category",
    "oem number": "oem_number", "oem brand": "oem_brand",
    "fram": "Fram", "donaldson": "Donaldson", "fleetguard": "Fleetguard",
    "baldwin": "Baldwin", "napa": "NAPA", "other": "Other", "notes": "notes",
}
FILTER_IMPORT_BRAND_KEYS = ("Fram", "Donaldson", "Fleetguard", "Baldwin", "NAPA")

PARTS_LIST_BRANDS = ["Baldwin", "Donaldson", "Fleetguard", "NAPA", "Fram"]
DEFAULT_PARTS_LIST_SORT = "type"

FILTER_TYPE_LABELS = {
    "oil": "Oil filter", "air": "Air filter", "air inner": "Air filter (inner)",
    "air outer": "Air filter (outer)", "air, pre": "Air filter (pre-filter)",
    "cabin": "Cabin filter", "fuel": "Fuel filter", "hydraulic": "Hydraulic filter",
    "hydraulic pilot": "Hydraulic filter (pilot)", "transmission": "Transmission filter",
    "def": "DEF filter", "breather": "Breather filter", "coolant": "Coolant filter",
    "service kit": "Service kit", "axle": "Axle filter", "water": "Water filter",
}


def _filter_item_label(category_name):
    return FILTER_TYPE_LABELS.get(category_name.strip().lower(), f"{category_name.strip()} filter")


def _parse_filter_workbook(file_bytes, db):
    """Parse a filter-assignment spreadsheet (Unit / Main P/N / Type / OEM
    Number / OEM Brand / Fram / Donaldson / Fleetguard / Baldwin / NAPA /
    Other / Notes) into a plan of shared catalog categories/items/alternates
    plus equipment links and per-unit OEM info fields. Doesn't write
    anything — safe to call repeatedly for preview."""
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception:
        return {"error": "Couldn't read that file — make sure it's a real .xlsx spreadsheet."}

    if FILTER_IMPORT_SHEET not in wb.sheetnames:
        return {"error": f"No sheet named \"{FILTER_IMPORT_SHEET}\" found in that file."}
    ws = wb[FILTER_IMPORT_SHEET]

    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        return {"error": f"The \"{FILTER_IMPORT_SHEET}\" sheet is empty."}

    col_map = {}
    for idx, header_cell in enumerate(header_row):
        if header_cell is None:
            continue
        key = FILTER_IMPORT_HEADERS.get(str(header_cell).strip().lower())
        if key:
            col_map[key] = idx

    if not {"unit", "value", "category"}.issubset(col_map):
        return {"error": "Couldn't find Unit, Main P/N, and Type columns in the header row."}

    def cell(row, key):
        idx = col_map.get(key)
        if idx is None or idx >= len(row) or row[idx] is None:
            return None
        value = str(row[idx]).strip()
        return value or None

    existing_units = {r["unit_number"] for r in db.execute("SELECT unit_number FROM equipment")}

    categories = {}
    skipped_no_value = {}
    skipped_missing_unit = {}
    oem_infos = []

    for row in rows_iter:
        unit = cell(row, "unit")
        if not unit or unit.lower() == "false":
            continue

        category_name = cell(row, "category")
        value = cell(row, "value")
        if not value:
            # Main P/N is sometimes left blank even though one of the brand
            # columns has the only part number recorded for this filter —
            # fall back to the first one present instead of dropping the row.
            for brand in FILTER_IMPORT_BRAND_KEYS:
                value = cell(row, brand)
                if value:
                    break
        if not value or not category_name:
            skipped_no_value[unit] = skipped_no_value.get(unit, 0) + 1
            continue
        if unit not in existing_units:
            skipped_missing_unit[unit] = skipped_missing_unit.get(unit, 0) + 1
            continue

        value_key = value.upper()
        cat = categories.setdefault(category_name, {})
        item = cat.setdefault(value_key, {
            "value": value, "notes": None, "alternates": set(), "units": set(),
        })
        item["units"].add(unit)

        notes = cell(row, "notes")
        if notes and not item["notes"]:
            item["notes"] = notes

        for brand in FILTER_IMPORT_BRAND_KEYS:
            brand_value = cell(row, brand)
            if brand_value and brand_value.upper() != value_key:
                item["alternates"].add((brand, brand_value))

        other = cell(row, "Other") or ""
        for part in other.split(","):
            part = part.strip()
            if part and part.upper() != value_key:
                item["alternates"].add(("Other", part))

        oem_number = cell(row, "oem_number")
        if oem_number and oem_number.upper() != value_key:
            oem_brand = cell(row, "oem_brand")
            oem_infos.append((unit, f"{oem_number} ({oem_brand})" if oem_brand else oem_number))

    return {
        "error": None,
        "categories": categories,
        "skipped_no_value": skipped_no_value,
        "skipped_missing_unit": skipped_missing_unit,
        "oem_infos": oem_infos,
    }


def _summarize_filter_plan(plan):
    categories = []
    total_items = 0
    total_alternates = 0
    total_links = 0
    for category_name in sorted(plan["categories"]):
        items = sorted(plan["categories"][category_name].values(), key=lambda i: i["value"])
        for item in items:
            item["alternates"] = sorted(item["alternates"])
            item["units"] = sorted(item["units"])
        alt_count = sum(len(i["alternates"]) for i in items)
        link_count = sum(len(i["units"]) for i in items)
        categories.append({
            "name": category_name, "parts": items,
            "alt_count": alt_count, "link_count": link_count,
        })
        total_items += len(items)
        total_alternates += alt_count
        total_links += link_count
    return {
        "categories": categories, "total_items": total_items,
        "total_alternates": total_alternates, "total_links": total_links,
        "total_oem_infos": len(plan["oem_infos"]),
    }


def _commit_filter_plan(plan, db):
    equipment_ids = {r["unit_number"]: r["id"] for r in db.execute("SELECT id, unit_number FROM equipment")}
    existing_categories = {r["name"].strip().lower(): r["id"] for r in db.execute("SELECT id, name FROM catalog_categories")}

    result = {
        "categories_created": 0, "items_created": 0, "items_reused": 0,
        "alternates_created": 0, "links_created": 0, "oem_items_created": 0,
    }

    for category_name, items in plan["categories"].items():
        category_id = existing_categories.get(category_name.strip().lower())
        if category_id is None:
            cur = db.execute("INSERT INTO catalog_categories (name) VALUES (?)", (category_name,))
            category_id = cur.lastrowid
            existing_categories[category_name.strip().lower()] = category_id
            result["categories_created"] += 1

        existing_items = {
            (r["value"] or "").strip().upper(): r["id"]
            for r in db.execute("SELECT id, value FROM catalog_items WHERE category_id = ?", (category_id,))
        }

        for value_key, item in items.items():
            catalog_item_id = existing_items.get(value_key)
            if catalog_item_id is None:
                cur = db.execute(
                    "INSERT INTO catalog_items (category_id, label, value, notes) VALUES (?, ?, ?, ?)",
                    (category_id, _filter_item_label(category_name), item["value"], item["notes"]),
                )
                catalog_item_id = cur.lastrowid
                existing_items[value_key] = catalog_item_id
                result["items_created"] += 1
            else:
                result["items_reused"] += 1

            existing_alts = {
                r["value"].strip().upper()
                for r in db.execute(
                    "SELECT value FROM catalog_item_alternates WHERE catalog_item_id = ?", (catalog_item_id,)
                )
            }
            for brand, alt_value in item["alternates"]:
                if alt_value.strip().upper() in existing_alts:
                    continue
                db.execute(
                    "INSERT INTO catalog_item_alternates (catalog_item_id, value, notes) VALUES (?, ?, ?)",
                    (catalog_item_id, alt_value, brand),
                )
                existing_alts.add(alt_value.strip().upper())
                result["alternates_created"] += 1

            for unit in item["units"]:
                equipment_id = equipment_ids.get(unit)
                if equipment_id is None:
                    continue
                cur = db.execute(
                    "INSERT OR IGNORE INTO equipment_catalog_items (equipment_id, catalog_item_id) VALUES (?, ?)",
                    (equipment_id, catalog_item_id),
                )
                if cur.rowcount:
                    result["links_created"] += 1

    for unit, info_value in plan["oem_infos"]:
        equipment_id = equipment_ids.get(unit)
        if equipment_id is None:
            continue
        exists = db.execute(
            "SELECT 1 FROM equipment_info_items WHERE equipment_id = ? AND label = ? AND value = ?",
            (equipment_id, "OEM part number", info_value),
        ).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO equipment_info_items (equipment_id, label, value) VALUES (?, ?, ?)",
            (equipment_id, "OEM part number", info_value),
        )
        result["oem_items_created"] += 1

    db.commit()
    return result


def _category_or_404(db, category_id):
    category = db.execute(
        "SELECT * FROM catalog_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if category is None:
        abort(404)
    return category


def _item_or_404(db, item_id):
    item = db.execute("SELECT * FROM catalog_items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def _alternate_or_404(db, alternate_id):
    alt = db.execute(
        "SELECT * FROM catalog_item_alternates WHERE id = ?", (alternate_id,)
    ).fetchone()
    if alt is None:
        abort(404)
    return alt


def get_linked_tree(db, equipment_id):
    """Return (categories, items_by_category, alternates_by_item,
    other_equipment_by_item, linked_ids) for the catalog items one equipment
    item is linked to, grouped by category — used to render an equipment
    item's Maintenance Info section. other_equipment_by_item maps each
    catalog_item_id to the *other* equipment (not this one) linked to it."""
    items = db.execute(
        """SELECT catalog_items.*, equipment_catalog_items.quantity AS link_quantity,
                  equipment_catalog_items.quantity_unit AS link_quantity_unit
           FROM catalog_items
           JOIN equipment_catalog_items
             ON equipment_catalog_items.catalog_item_id = catalog_items.id
           WHERE equipment_catalog_items.equipment_id = ?
           ORDER BY catalog_items.sort_order, catalog_items.label""",
        (equipment_id,),
    ).fetchall()

    if not items:
        return [], {}, {}, {}, set()

    category_ids = sorted({row["category_id"] for row in items})
    placeholders = ",".join("?" * len(category_ids))
    categories = db.execute(
        f"""SELECT * FROM catalog_categories
            WHERE id IN ({placeholders})
            ORDER BY sort_order, name""",
        category_ids,
    ).fetchall()

    items_by_category = {}
    for row in items:
        items_by_category.setdefault(row["category_id"], []).append(row)

    item_ids = [row["id"] for row in items]
    item_placeholders = ",".join("?" * len(item_ids))
    alt_rows = db.execute(
        f"""SELECT * FROM catalog_item_alternates
            WHERE catalog_item_id IN ({item_placeholders})
            ORDER BY sort_order, value""",
        item_ids,
    ).fetchall()
    alternates_by_item = {}
    for row in alt_rows:
        alternates_by_item.setdefault(row["catalog_item_id"], []).append(row)

    other_link_rows = db.execute(
        f"""SELECT equipment_catalog_items.catalog_item_id AS catalog_item_id,
                   equipment.id AS equipment_id, equipment.unit_number AS unit_number
            FROM equipment_catalog_items
            JOIN equipment ON equipment.id = equipment_catalog_items.equipment_id
            WHERE equipment_catalog_items.catalog_item_id IN ({item_placeholders})
              AND equipment_catalog_items.equipment_id != ?
            ORDER BY equipment.unit_number""",
        item_ids + [equipment_id],
    ).fetchall()
    other_equipment_by_item = {}
    for row in other_link_rows:
        other_equipment_by_item.setdefault(row["catalog_item_id"], []).append(row)

    return categories, items_by_category, alternates_by_item, other_equipment_by_item, {row["id"] for row in items}


def _next_or(default_endpoint, **default_kwargs):
    next_url = request.values.get("next")
    return redirect(next_url) if next_url else redirect(url_for(default_endpoint, **default_kwargs))


@bp.route("/")
@login_required
def index():
    db = get_db()
    categories = db.execute(
        "SELECT * FROM catalog_categories ORDER BY sort_order, name"
    ).fetchall()

    items_by_category = {}
    alternates_by_item = {}
    equipment_by_item = {}
    if categories:
        category_ids = [c["id"] for c in categories]
        placeholders = ",".join("?" * len(category_ids))
        items = db.execute(
            f"""SELECT * FROM catalog_items
                WHERE category_id IN ({placeholders})
                ORDER BY sort_order, label""",
            category_ids,
        ).fetchall()
        for row in items:
            items_by_category.setdefault(row["category_id"], []).append(row)

        if items:
            item_ids = [row["id"] for row in items]
            item_placeholders = ",".join("?" * len(item_ids))
            alt_rows = db.execute(
                f"""SELECT * FROM catalog_item_alternates
                    WHERE catalog_item_id IN ({item_placeholders})
                    ORDER BY sort_order, value""",
                item_ids,
            ).fetchall()
            for row in alt_rows:
                alternates_by_item.setdefault(row["catalog_item_id"], []).append(row)

            link_rows = db.execute(
                f"""SELECT equipment_catalog_items.catalog_item_id AS catalog_item_id,
                           equipment.id AS equipment_id, equipment.unit_number AS unit_number
                    FROM equipment_catalog_items
                    JOIN equipment ON equipment.id = equipment_catalog_items.equipment_id
                    WHERE equipment_catalog_items.catalog_item_id IN ({item_placeholders})
                    ORDER BY equipment.unit_number""",
                item_ids,
            ).fetchall()
            for row in link_rows:
                equipment_by_item.setdefault(row["catalog_item_id"], []).append(row)

    return render_template(
        "catalog/index.html",
        categories=categories,
        items_by_category=items_by_category,
        alternates_by_item=alternates_by_item,
        equipment_by_item=equipment_by_item,
    )


def _stock_sort_value(value):
    """Sort key for supplier_stock, which is usually an int but can be
    overflow text like "10+" (Bytown doesn't track exact counts past a
    point) -- sort by the leading number either way, blanks last-ish."""
    if value is None:
        return -1
    if isinstance(value, int):
        return value
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else -1


def _brand_for_alternate(alt_notes):
    notes = (alt_notes or "").strip().lower()
    return next((b for b in PARTS_LIST_BRANDS if b.lower() == notes), None)


@bp.route("/parts-list")
@login_required
def parts_list():
    db = get_db()

    items = db.execute(
        """SELECT catalog_items.id, catalog_items.value AS part_number,
                  catalog_items.label, catalog_items.location AS location,
                  catalog_items.supplier_stock AS supplier_stock,
                  catalog_items.napa_stock AS napa_stock,
                  catalog_categories.name AS type_name
           FROM catalog_items
           JOIN catalog_categories ON catalog_categories.id = catalog_items.category_id"""
    ).fetchall()

    rows = []
    if items:
        item_ids = [row["id"] for row in items]
        placeholders = ",".join("?" * len(item_ids))

        alt_rows = db.execute(
            f"""SELECT catalog_item_id, value, notes FROM catalog_item_alternates
                WHERE catalog_item_id IN ({placeholders})""",
            item_ids,
        ).fetchall()
        alternates_by_item = {}
        for row in alt_rows:
            alternates_by_item.setdefault(row["catalog_item_id"], []).append(row)

        link_rows = db.execute(
            f"""SELECT equipment_catalog_items.catalog_item_id AS catalog_item_id,
                       equipment.id AS equipment_id, equipment.unit_number AS unit_number
                FROM equipment_catalog_items
                JOIN equipment ON equipment.id = equipment_catalog_items.equipment_id
                WHERE equipment_catalog_items.catalog_item_id IN ({placeholders})
                ORDER BY equipment.unit_number""",
            item_ids,
        ).fetchall()
        units_by_item = {}
        for row in link_rows:
            units_by_item.setdefault(row["catalog_item_id"], []).append(row)

        flagged_ids = {
            row["catalog_item_id"]
            for row in db.execute(
                f"""SELECT DISTINCT catalog_item_id FROM catalog_item_specs
                    WHERE catalog_item_id IN ({placeholders}) AND flagged = 1""",
                item_ids,
            ).fetchall()
        }

        for item in items:
            brands = {}
            for alt in alternates_by_item.get(item["id"], []):
                brand = _brand_for_alternate(alt["notes"])
                if brand:
                    brands.setdefault(brand, []).append(alt["value"])

            units = units_by_item.get(item["id"], [])
            rows.append({
                "id": item["id"],
                "part_number": item["part_number"] or item["label"],
                "type": item["type_name"],
                "location": item["location"],
                "supplier_stock": item["supplier_stock"],
                "napa_stock": item["napa_stock"],
                "brands": {brand: ", ".join(values) for brand, values in brands.items()},
                "units": units,
                "units_count": len(units),
                "flagged": item["id"] in flagged_ids,
            })

    sort = request.args.get("sort", DEFAULT_PARTS_LIST_SORT)
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"

    brand_sort_keys = {b.lower(): b for b in PARTS_LIST_BRANDS}
    sort_keys = {"part_number", "type", "location", "supplier_stock", "napa_stock", "units_count", "used_by", "flagged", *brand_sort_keys}
    if sort not in sort_keys:
        sort = DEFAULT_PARTS_LIST_SORT

    def primary_key(row):
        if sort == "part_number":
            return (row["part_number"] or "").lower()
        if sort == "type":
            return (row["type"] or "").lower()
        if sort == "location":
            return (row["location"] or "").lower()
        if sort == "supplier_stock":
            return _stock_sort_value(row["supplier_stock"])
        if sort == "napa_stock":
            return _stock_sort_value(row["napa_stock"])
        if sort == "units_count":
            return row["units_count"]
        if sort == "used_by":
            return ", ".join(u["unit_number"] for u in row["units"]).lower()
        if sort == "flagged":
            return row["flagged"]
        brand = brand_sort_keys[sort]
        return row["brands"].get(brand, "").lower()

    # Stable two-pass sort: break ties by part number (ascending) regardless
    # of the chosen primary sort or direction, so equal rows don't jump
    # around between clicks.
    rows.sort(key=lambda r: (r["part_number"] or "").lower())
    rows.sort(key=primary_key, reverse=(direction == "desc"))

    return render_template(
        "catalog/parts_list.html",
        rows=rows, sort=sort, dir=direction, brand_columns=PARTS_LIST_BRANDS,
        q=request.args.get("q", ""),
    )


@bp.route("/categories/new", methods=("GET", "POST"))
@login_required
def new_category():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        error = None if name else "Category name is required."

        if error is None:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO catalog_categories (name) VALUES (?)", (name,)
                )
                db.commit()
                flash(f"Added category {name}.", "success")
                return redirect(url_for("catalog.index", open=cur.lastrowid, _anchor=f"category-{cur.lastrowid}"))
            except sqlite3.IntegrityError:
                error = "A category with that name already exists."

        flash(error, "error")
        return render_template("catalog/category_form.html", category={"name": name}, mode="new")

    return render_template("catalog/category_form.html", category={}, mode="new")


@bp.route("/categories/<int:category_id>/edit", methods=("GET", "POST"))
@login_required
def edit_category(category_id):
    db = get_db()
    category = _category_or_404(db, category_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        error = None if name else "Category name is required."

        if error is None:
            db.execute("UPDATE catalog_categories SET name = ? WHERE id = ?", (name, category_id))
            db.commit()
            flash("Category updated.", "success")
            return redirect(url_for("catalog.index", open=category_id, _anchor=f"category-{category_id}"))

        flash(error, "error")
        return render_template(
            "catalog/category_form.html", category={"id": category_id, "name": name}, mode="edit"
        )

    return render_template("catalog/category_form.html", category=dict(category), mode="edit")


@bp.route("/categories/<int:category_id>/delete", methods=("POST",))
@login_required
def delete_category(category_id):
    db = get_db()
    category = _category_or_404(db, category_id)
    db.execute("DELETE FROM catalog_categories WHERE id = ?", (category_id,))
    db.commit()
    flash(f"Removed category {category['name']}.", "success")
    return redirect(url_for("catalog.index"))


def _read_item_form(form):
    return {
        "label": form.get("label", "").strip(),
        "value": form.get("value", "").strip() or None,
        "notes": form.get("notes", "").strip() or None,
        "location": form.get("location", "").strip() or None,
        # Left as text (not cast to int) since Bytown reports overflow
        # quantities like "10+" rather than an exact number past a point.
        # SQLite's INTEGER affinity still stores plain numbers as integers.
        "supplier_stock": form.get("supplier_stock", "").strip() or None,
        "napa_stock": form.get("napa_stock", "").strip() or None,
    }


@bp.route("/categories/<int:category_id>/items/new", methods=("GET", "POST"))
@login_required
def new_item(category_id):
    db = get_db()
    category = _category_or_404(db, category_id)

    if request.method == "POST":
        data = _read_item_form(request.form)
        error = None if data["label"] else "Label is required."

        if error is None:
            cur = db.execute(
                "INSERT INTO catalog_items (category_id, label, value, notes, location, supplier_stock, napa_stock) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (category_id, data["label"], data["value"], data["notes"], data["location"], data["supplier_stock"], data["napa_stock"]),
            )
            db.commit()
            flash(f"Added {data['label']}.", "success")
            return redirect(url_for(
                "catalog.index", open=category_id, open_item=cur.lastrowid, _anchor=f"item-{cur.lastrowid}",
            ))

        flash(error, "error")
        return render_template(
            "catalog/item_form.html", category=category, item=data, mode="new"
        )

    return render_template("catalog/item_form.html", category=category, item={}, mode="new")


@bp.route("/items/<int:item_id>/edit", methods=("GET", "POST"))
@login_required
def edit_item(item_id):
    db = get_db()
    item = _item_or_404(db, item_id)
    category = _category_or_404(db, item["category_id"])
    next_url = request.values.get("next")

    if request.method == "POST":
        data = _read_item_form(request.form)
        error = None if data["label"] else "Label is required."

        if error is None:
            db.execute(
                "UPDATE catalog_items SET label=?, value=?, notes=?, location=?, supplier_stock=?, napa_stock=? WHERE id=?",
                (data["label"], data["value"], data["notes"], data["location"], data["supplier_stock"], data["napa_stock"], item_id),
            )
            db.commit()
            flash("Updated.", "success")
            return redirect(next_url) if next_url else redirect(url_for("catalog.item_detail", item_id=item_id))

        flash(error, "error")
        data["id"] = item_id
        return render_template(
            "catalog/item_form.html", category=category, item=data, mode="edit", next=next_url
        )

    return render_template(
        "catalog/item_form.html", category=category, item=dict(item), mode="edit", next=next_url
    )


@bp.route("/items/<int:item_id>")
@login_required
def item_detail(item_id):
    db = get_db()
    item = _item_or_404(db, item_id)
    category = _category_or_404(db, item["category_id"])

    alts = db.execute(
        "SELECT * FROM catalog_item_alternates WHERE catalog_item_id = ? ORDER BY sort_order, value",
        (item_id,),
    ).fetchall()

    used_by = db.execute(
        """SELECT equipment.id AS equipment_id, equipment.unit_number AS unit_number
           FROM equipment_catalog_items
           JOIN equipment ON equipment.id = equipment_catalog_items.equipment_id
           WHERE equipment_catalog_items.catalog_item_id = ?
           ORDER BY equipment.unit_number""",
        (item_id,),
    ).fetchall()

    specs = db.execute(
        "SELECT * FROM catalog_item_specs WHERE catalog_item_id = ? ORDER BY sort_order, id",
        (item_id,),
    ).fetchall()

    return render_template(
        "catalog/item_detail.html",
        item=item, category=category, alts=alts, used_by=used_by, specs=specs,
    )


@bp.route("/items/<int:item_id>/delete", methods=("POST",))
@login_required
def delete_item(item_id):
    db = get_db()
    item = _item_or_404(db, item_id)
    category_id = item["category_id"]
    db.execute("DELETE FROM catalog_items WHERE id = ?", (item_id,))
    db.commit()
    flash(f"Removed {item['label']} from the catalog.", "success")
    return redirect(url_for("catalog.index", open=category_id, _anchor=f"category-{category_id}"))


@bp.route("/items/<int:item_id>/alternates/new", methods=("POST",))
@login_required
def new_alternate(item_id):
    db = get_db()
    item = _item_or_404(db, item_id)
    value = request.form.get("value", "").strip()
    notes = request.form.get("notes", "").strip() or None

    if value:
        db.execute(
            "INSERT INTO catalog_item_alternates (catalog_item_id, value, notes) VALUES (?, ?, ?)",
            (item_id, value, notes),
        )
        db.commit()
        flash("Alternate added.", "success")
    else:
        flash("An alternate value is required.", "error")

    return _next_or("catalog.index", open=item["category_id"], open_item=item_id, _anchor=f"item-{item_id}")


@bp.route("/alternates/<int:alternate_id>/delete", methods=("POST",))
@login_required
def delete_alternate(alternate_id):
    db = get_db()
    alt = _alternate_or_404(db, alternate_id)
    item = db.execute(
        "SELECT category_id FROM catalog_items WHERE id = ?", (alt["catalog_item_id"],)
    ).fetchone()
    db.execute("DELETE FROM catalog_item_alternates WHERE id = ?", (alternate_id,))
    db.commit()
    flash("Alternate removed.", "success")
    return _next_or(
        "catalog.index", open=item["category_id"], open_item=alt["catalog_item_id"],
        _anchor=f"item-{alt['catalog_item_id']}",
    )


@bp.route("/items/<int:item_id>/specs/new", methods=("POST",))
@login_required
def new_spec(item_id):
    db = get_db()
    _item_or_404(db, item_id)
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip() or None
    flagged = 1 if request.form.get("flagged") else 0

    if label:
        db.execute(
            "INSERT INTO catalog_item_specs (catalog_item_id, label, value, flagged) VALUES (?, ?, ?, ?)",
            (item_id, label, value, flagged),
        )
        db.commit()
        flash(f"Added {label}.", "success")
    else:
        flash("A name is required.", "error")

    return redirect(url_for("catalog.item_detail", item_id=item_id))


@bp.route("/specs/<int:spec_id>/delete", methods=("POST",))
@login_required
def delete_spec(spec_id):
    db = get_db()
    spec = db.execute("SELECT * FROM catalog_item_specs WHERE id = ?", (spec_id,)).fetchone()
    if spec is None:
        abort(404)
    db.execute("DELETE FROM catalog_item_specs WHERE id = ?", (spec_id,))
    db.commit()
    flash("Removed.", "success")
    return redirect(url_for("catalog.item_detail", item_id=spec["catalog_item_id"]))


@bp.route("/import")
@login_required
def import_form():
    return render_template("catalog/import.html")


@bp.route("/import", methods=("POST",))
@login_required
def import_preview():
    file = request.files.get("xlsx_file")
    if not file or not file.filename:
        flash("Choose a spreadsheet file to import.", "error")
        return redirect(url_for("catalog.import_form"))

    file_bytes = file.read()
    plan = _parse_filter_workbook(file_bytes, get_db())
    if plan["error"]:
        flash(plan["error"], "error")
        return redirect(url_for("catalog.import_form"))

    if not plan["categories"] and not plan["oem_infos"]:
        flash("No usable filter rows found in that file.", "error")
        return redirect(url_for("catalog.import_form"))

    return render_template(
        "catalog/import_preview.html",
        summary=_summarize_filter_plan(plan),
        skipped_no_value=plan["skipped_no_value"],
        skipped_missing_unit=plan["skipped_missing_unit"],
        file_data=base64.b64encode(file_bytes).decode("ascii"),
    )


@bp.route("/import/confirm", methods=("POST",))
@login_required
def import_confirm():
    try:
        file_bytes = base64.b64decode(request.form.get("file_data", ""))
    except (ValueError, TypeError):
        flash("Something went wrong reading that file — try uploading it again.", "error")
        return redirect(url_for("catalog.import_form"))

    db = get_db()
    plan = _parse_filter_workbook(file_bytes, db)
    if plan["error"]:
        flash(plan["error"], "error")
        return redirect(url_for("catalog.import_form"))

    result = _commit_filter_plan(plan, db)
    flash(
        f"Imported {result['items_created']} new catalog item{'' if result['items_created'] == 1 else 's'} "
        f"({result['items_reused']} already existed), {result['alternates_created']} alternate part numbers, "
        f"{result['links_created']} equipment links, and {result['oem_items_created']} OEM info fields "
        f"across {result['categories_created']} new categories.",
        "success",
    )
    return redirect(url_for("catalog.index"))
