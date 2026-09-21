import base64
import io
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from openpyxl import load_workbook

from .. import floorplan, warehouse
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


def _record_location_change(db, item_id, old_location, new_location):
    """Log a Location edit for the item's Location History, if it actually
    changed. Called from every path that can touch this field, so the log
    stays complete regardless of whether the edit came from the Inventory
    Audit inline editor or the regular Edit item form."""
    if old_location == new_location:
        return
    db.execute(
        """INSERT INTO catalog_item_location_history
           (catalog_item_id, old_location, new_location, changed_by) VALUES (?, ?, ?, ?)""",
        (item_id, old_location, new_location, g.user["username"] if g.user else None),
    )


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


def _build_parts_list_rows(db):
    """Shared row-building + sorting for the Parts List and Inventory Audit
    pages -- same data, same search/sort behavior, different templates."""
    items = db.execute(
        """SELECT catalog_items.id, catalog_items.value AS part_number,
                  catalog_items.label, catalog_items.location AS location,
                  catalog_items.quantity_on_hand AS quantity_on_hand,
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
                "quantity_on_hand": item["quantity_on_hand"],
                "supplier_stock": item["supplier_stock"],
                "napa_stock": item["napa_stock"],
                "brands": {brand: ", ".join(values) for brand, values in brands.items()},
                "units": units,
                "units_count": len(units),
                "flagged": item["id"] in flagged_ids,
            })

    all_units = [
        row["unit_number"]
        for row in db.execute("SELECT unit_number FROM equipment ORDER BY unit_number").fetchall()
    ]

    sort = request.args.get("sort", DEFAULT_PARTS_LIST_SORT)
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"

    brand_sort_keys = {b.lower(): b for b in PARTS_LIST_BRANDS}
    sort_keys = {"part_number", "type", "location", "quantity_on_hand", "supplier_stock", "napa_stock", "units_count", "used_by", "flagged", *brand_sort_keys}
    if sort not in sort_keys:
        sort = DEFAULT_PARTS_LIST_SORT

    def primary_key(row):
        if sort == "part_number":
            return (row["part_number"] or "").lower()
        if sort == "type":
            return (row["type"] or "").lower()
        if sort == "location":
            return (row["location"] or "").lower()
        if sort == "quantity_on_hand":
            return row["quantity_on_hand"] if row["quantity_on_hand"] is not None else -1
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

    return rows, sort, direction, all_units


@bp.route("/parts-list")
@login_required
def parts_list():
    db = get_db()
    rows, sort, direction, all_units = _build_parts_list_rows(db)

    return render_template(
        "catalog/parts_list.html",
        rows=rows, sort=sort, dir=direction, brand_columns=PARTS_LIST_BRANDS,
        q=request.args.get("q", ""), unit=request.args.get("unit", ""),
        all_units=all_units,
    )


@bp.route("/inventory-audit")
@login_required
def inventory_audit():
    db = get_db()
    rows, sort, direction, all_units = _build_parts_list_rows(db)

    return render_template(
        "catalog/inventory_audit.html",
        rows=rows, sort=sort, dir=direction, brand_columns=PARTS_LIST_BRANDS,
        q=request.args.get("q", ""), unit=request.args.get("unit", ""),
        all_units=all_units,
    )


@bp.route("/items/<int:item_id>/location", methods=("POST",))
@login_required
def update_item_location(item_id):
    """Lightweight endpoint for the Inventory Audit page's inline location
    editor -- just this one column, saved via fetch without a page reload.
    Free text only (no aisle/bank/shelf picker in this inline editor), so
    location_code is cleared -- it can no longer be trusted to match once
    location has been hand-edited here. Use the full Edit item form to
    reassign a warehouse shelf position."""
    db = get_db()
    item = _item_or_404(db, item_id)
    location = request.form.get("location", "").strip() or None
    _record_location_change(db, item_id, item["location"], location)
    db.execute("UPDATE catalog_items SET location=?, location_code=NULL WHERE id=?", (location, item_id))
    db.commit()
    return {"ok": True, "location": location}


@bp.route("/items/<int:item_id>/quantity-on-hand", methods=("POST",))
@login_required
def update_item_quantity_on_hand(item_id):
    """Lightweight endpoint for the Inventory Audit page's inline quantity
    editor -- no history log for this one, just the latest count."""
    db = get_db()
    _item_or_404(db, item_id)
    quantity = _parse_quantity_on_hand(request.form)
    db.execute("UPDATE catalog_items SET quantity_on_hand=? WHERE id=?", (quantity, item_id))
    db.commit()
    return {"ok": True, "quantity_on_hand": quantity}


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


def _parse_quantity_on_hand(form):
    raw = form.get("quantity_on_hand", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_item_location(form):
    """Reads the item form's Location picker: either a warehouse Aisle/
    Bank/Shelf pick (composed into the human-readable + machine-sortable
    pair via warehouse.compose) or freeform text. Returns
    (location, location_code, error, raw) -- raw carries back the picker's
    individual fields so the form can be redisplayed with the same
    selection/text on a validation error."""
    mode = form.get("location_mode", "custom")
    raw = {
        "location_mode": mode,
        "location_aisle": form.get("location_aisle", ""),
        "location_bank": form.get("location_bank", ""),
        "location_shelf": form.get("location_shelf", ""),
        "location_custom": form.get("location_custom", "").strip(),
    }

    if mode == "warehouse":
        aisle, bank, shelf = raw["location_aisle"], raw["location_bank"], raw["location_shelf"]
        if not (aisle or bank or shelf):
            return None, None, None, raw  # nothing picked -- location is just unset, not an error
        if not (aisle and bank and shelf):
            return None, None, "Choose an aisle, bank, and shelf, or switch to a custom location.", raw
        try:
            human, code = warehouse.compose(aisle, bank, shelf)
        except ValueError:
            return None, None, "That aisle/bank/shelf combination doesn't exist.", raw
        return human, code, None, raw

    return raw["location_custom"] or None, None, None, raw


def _read_item_form(form):
    location, location_code, location_error, location_raw = _read_item_location(form)
    data = {
        "label": form.get("label", "").strip(),
        "value": form.get("value", "").strip() or None,
        "notes": form.get("notes", "").strip() or None,
        "location": location,
        "location_code": location_code,
        "quantity_on_hand": _parse_quantity_on_hand(form),
        # Left as text (not cast to int) since Bytown reports overflow
        # quantities like "10+" rather than an exact number past a point.
        # SQLite's INTEGER affinity still stores plain numbers as integers.
        "supplier_stock": form.get("supplier_stock", "").strip() or None,
        "napa_stock": form.get("napa_stock", "").strip() or None,
    }
    data.update(location_raw)
    data["_location_error"] = location_error
    return data


def _location_picker_defaults(item):
    """Derive the Location picker's starting state (mode + individual
    fields) from a stored DB row, for prefilling the Edit form. Warehouse
    positions round-trip through location_code; anything else (including
    legacy freeform text) opens in custom mode with that text intact."""
    parsed = warehouse.parse_code(item["location_code"]) if item["location_code"] else None
    # The picker only edits the default room for now (no room selector in
    # the UI yet) -- a code from some other room still displays fine as
    # plain text via the "Location" info field, it just opens here in
    # custom mode rather than mis-mapping onto the wrong room's aisles.
    if parsed and parsed[0] == warehouse.DEFAULT_ROOM:
        _room, aisle, bank, shelf = parsed
        return {
            "location_mode": "warehouse", "location_aisle": aisle,
            "location_bank": bank, "location_shelf": str(shelf), "location_custom": "",
        }
    return {
        "location_mode": "custom", "location_aisle": "", "location_bank": "",
        "location_shelf": "", "location_custom": item["location"] or "",
    }


@bp.route("/categories/<int:category_id>/items/new", methods=("GET", "POST"))
@login_required
def new_item(category_id):
    db = get_db()
    category = _category_or_404(db, category_id)

    if request.method == "POST":
        data = _read_item_form(request.form)
        error = data["_location_error"] or (None if data["label"] else "Label is required.")

        if error is None:
            cur = db.execute(
                """INSERT INTO catalog_items
                   (category_id, label, value, notes, location, location_code, quantity_on_hand, supplier_stock, napa_stock)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (category_id, data["label"], data["value"], data["notes"], data["location"],
                 data["location_code"], data["quantity_on_hand"], data["supplier_stock"], data["napa_stock"]),
            )
            db.commit()
            flash(f"Added {data['label']}.", "success")
            return redirect(url_for(
                "catalog.index", open=category_id, open_item=cur.lastrowid, _anchor=f"item-{cur.lastrowid}",
            ))

        flash(error, "error")
        return render_template(
            "catalog/item_form.html", category=category, item=data, mode="new",
            warehouse_aisles=warehouse.aisle_options(),
        )

    return render_template(
        "catalog/item_form.html", category=category, item={"location_mode": "warehouse"}, mode="new",
        warehouse_aisles=warehouse.aisle_options(),
    )


@bp.route("/items/<int:item_id>/edit", methods=("GET", "POST"))
@login_required
def edit_item(item_id):
    db = get_db()
    item = _item_or_404(db, item_id)
    category = _category_or_404(db, item["category_id"])
    next_url = request.values.get("next")

    if request.method == "POST":
        data = _read_item_form(request.form)
        error = data["_location_error"] or (None if data["label"] else "Label is required.")

        if error is None:
            _record_location_change(db, item_id, item["location"], data["location"])
            db.execute(
                """UPDATE catalog_items
                   SET label=?, value=?, notes=?, location=?, location_code=?, quantity_on_hand=?, supplier_stock=?, napa_stock=?
                   WHERE id=?""",
                (data["label"], data["value"], data["notes"], data["location"], data["location_code"],
                 data["quantity_on_hand"], data["supplier_stock"], data["napa_stock"], item_id),
            )
            db.commit()
            flash("Updated.", "success")
            return redirect(next_url) if next_url else redirect(url_for("catalog.item_detail", item_id=item_id))

        flash(error, "error")
        data["id"] = item_id
        alts = db.execute(
            "SELECT * FROM catalog_item_alternates WHERE catalog_item_id = ? ORDER BY sort_order, value",
            (item_id,),
        ).fetchall()
        return render_template(
            "catalog/item_form.html", category=category, item=data, mode="edit", next=next_url, alts=alts,
            warehouse_aisles=warehouse.aisle_options(),
        )

    alts = db.execute(
        "SELECT * FROM catalog_item_alternates WHERE catalog_item_id = ? ORDER BY sort_order, value",
        (item_id,),
    ).fetchall()
    item_data = dict(item)
    item_data.update(_location_picker_defaults(item))
    return render_template(
        "catalog/item_form.html", category=category, item=item_data, mode="edit", next=next_url, alts=alts,
        warehouse_aisles=warehouse.aisle_options(),
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

    location_history = db.execute(
        """SELECT * FROM catalog_item_location_history
           WHERE catalog_item_id = ? ORDER BY changed_at DESC, id DESC""",
        (item_id,),
    ).fetchall()

    floor_plan_svg = floor_plan_svg_dialog = shelf_elevation_svg = shelf_elevation_svg_dialog = room_label = None
    parsed_location = warehouse.parse_code(item["location_code"]) if item["location_code"] else None
    if parsed_location:
        room, aisle, bank, shelf = parsed_location
        room_label = warehouse.get_room(room)["label"]
        # Rendered twice (a small thumbnail plus a larger copy shown in the
        # expand-on-click dialog) -- each needs its own uid so their <defs>
        # filter ids don't collide as duplicates in the same HTML page.
        floor_plan_svg = floorplan.render_floor_plan(room=room, highlight=(aisle, bank), uid="fp-thumb")
        floor_plan_svg_dialog = floorplan.render_floor_plan(room=room, highlight=(aisle, bank), uid="fp-dialog")
        shelf_elevation_svg = floorplan.render_shelf_elevation(shelf, uid="se-thumb")
        shelf_elevation_svg_dialog = floorplan.render_shelf_elevation(shelf, uid="se-dialog")

    return render_template(
        "catalog/item_detail.html",
        item=item, category=category, alts=alts, used_by=used_by, specs=specs,
        location_history=location_history, floor_plan_svg=floor_plan_svg,
        floor_plan_svg_dialog=floor_plan_svg_dialog, shelf_elevation_svg=shelf_elevation_svg,
        shelf_elevation_svg_dialog=shelf_elevation_svg_dialog, room_label=room_label,
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


@bp.route("/alternates/<int:alternate_id>/edit", methods=("POST",))
@login_required
def edit_alternate(alternate_id):
    db = get_db()
    alt = _alternate_or_404(db, alternate_id)
    item = db.execute(
        "SELECT category_id FROM catalog_items WHERE id = ?", (alt["catalog_item_id"],)
    ).fetchone()
    value = request.form.get("value", "").strip()
    notes = request.form.get("notes", "").strip() or None

    if value:
        db.execute(
            "UPDATE catalog_item_alternates SET value=?, notes=? WHERE id=?",
            (value, notes, alternate_id),
        )
        db.commit()
        flash("Alternate updated.", "success")
    else:
        flash("An alternate value is required.", "error")

    return _next_or(
        "catalog.index", open=item["category_id"], open_item=alt["catalog_item_id"],
        _anchor=f"item-{alt['catalog_item_id']}",
    )


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
