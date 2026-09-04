import sqlite3
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required
from .catalog import get_linked_tree

bp = Blueprint("equipment", __name__, url_prefix="/equipment")

STATUSES = ["active", "in_shop", "retired"]
METER_TYPES = ["kilometers", "hours", "miles"]
FUEL_TYPES = ["gasoline", "diesel", "propane", "electric", "other"]
# Not enforced in the database — expected to grow over time.
EQUIPMENT_TYPES = [
    "Automotive", "Pickup Truck", "Heavy Truck", "Trailer", "Heavy Equipment",
    "Light Equipment", "Generator", "Boat", "Small Tool",
]

# Optional built-in fields — off by default, added/removed per equipment
# item the same way custom fields are. Order here is display order.
OPTIONAL_FIELDS = ["year", "serial_number", "license_plate", "fuel_type", "assigned_to", "notes"]
OPTIONAL_FIELD_LABELS = {
    "year": "Year",
    "serial_number": "Serial number",
    "license_plate": "License plate",
    "fuel_type": "Fuel type",
    "assigned_to": "Assigned to",
    "notes": "Notes",
}


@bp.route("/")
@login_required
def list_equipment():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")

    query = "SELECT * FROM equipment WHERE 1=1"
    params = []

    if q:
        query += """ AND (
            unit_number LIKE ? OR make LIKE ? OR model LIKE ?
            OR serial_number LIKE ? OR license_plate LIKE ? OR assigned_to LIKE ?
        )"""
        like = f"%{q}%"
        params += [like] * 6

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY unit_number"

    items = db.execute(query, params).fetchall()

    return render_template(
        "equipment/list.html",
        items=items,
        q=q,
        status=status,
        statuses=STATUSES,
    )


def _equipment_or_404(equipment_id):
    db = get_db()
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def _custom_fields_for(db, equipment_id):
    return db.execute(
        "SELECT * FROM equipment_custom_fields WHERE equipment_id = ? ORDER BY sort_order, label",
        (equipment_id,),
    ).fetchall()


def _enabled_fields_for(db, equipment_id):
    rows = db.execute(
        "SELECT field_name FROM equipment_enabled_fields WHERE equipment_id = ?", (equipment_id,)
    ).fetchall()
    return {row["field_name"] for row in rows}


def _info_items_for(db, equipment_id):
    return db.execute(
        "SELECT * FROM equipment_info_items WHERE equipment_id = ? ORDER BY sort_order, label",
        (equipment_id,),
    ).fetchall()


def record_meter_reading(db, equipment_id, reading, recorded_at, notes=None):
    """Insert a timestamped meter-history point and resync equipment.meter_reading
    to whichever recorded reading is chronologically most recent — so a
    backdated entry never clobbers a newer current value, but a same-day
    correction (last one wins) or a genuinely new high point always does."""
    db.execute(
        "INSERT INTO equipment_meter_readings (equipment_id, reading, recorded_at, notes) VALUES (?, ?, ?, ?)",
        (equipment_id, reading, recorded_at, notes),
    )
    latest = db.execute(
        """SELECT reading FROM equipment_meter_readings
           WHERE equipment_id = ?
           ORDER BY recorded_at DESC, id DESC
           LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    db.execute(
        "UPDATE equipment SET meter_reading = ?, updated_at = datetime('now') WHERE id = ?",
        (latest["reading"], equipment_id),
    )


def _resync_meter_reading(db, equipment_id):
    """Recompute equipment.meter_reading after a history row was deleted."""
    latest = db.execute(
        """SELECT reading FROM equipment_meter_readings
           WHERE equipment_id = ?
           ORDER BY recorded_at DESC, id DESC
           LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    db.execute(
        "UPDATE equipment SET meter_reading = ?, updated_at = datetime('now') WHERE id = ?",
        (latest["reading"] if latest else 0, equipment_id),
    )


def _meter_chart(readings):
    """Build simple SVG chart points (oldest to newest) from meter-history
    rows, scaled into a fixed viewBox. Returns None with fewer than 2 points."""
    if len(readings) < 2:
        return None
    values = [r["reading"] for r in readings]
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    width, height, pad = 320, 90, 8
    n = len(readings)
    points = []
    for i, r in enumerate(readings):
        x = pad + (width - 2 * pad) * (i / (n - 1))
        y = height - pad - (height - 2 * pad) * ((r["reading"] - lo) / span)
        points.append({"x": round(x, 1), "y": round(y, 1), "reading": r["reading"], "recorded_at": r["recorded_at"]})
    return {
        "points": points,
        "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
        "width": width,
        "height": height,
        "min": lo,
        "max": hi,
    }


@bp.route("/<int:equipment_id>")
@login_required
def detail(equipment_id):
    item = _equipment_or_404(equipment_id)
    db = get_db()
    records = db.execute(
        """SELECT * FROM maintenance_records
           WHERE equipment_id = ?
           ORDER BY service_date DESC, id DESC""",
        (equipment_id,),
    ).fetchall()
    custom_fields = _custom_fields_for(db, equipment_id)
    enabled_fields = _enabled_fields_for(db, equipment_id)
    info_items = _info_items_for(db, equipment_id)
    categories, items_by_category, alternates_by_item, _linked_ids = get_linked_tree(db, equipment_id)
    meter_readings = db.execute(
        """SELECT * FROM equipment_meter_readings
           WHERE equipment_id = ?
           ORDER BY recorded_at, id""",
        (equipment_id,),
    ).fetchall()
    return render_template(
        "equipment/detail.html",
        item=item,
        records=records,
        custom_fields=custom_fields,
        enabled_fields=enabled_fields,
        optional_field_labels=OPTIONAL_FIELD_LABELS,
        categories=categories,
        items_by_category=items_by_category,
        alternates_by_item=alternates_by_item,
        info_items=info_items,
        meter_readings=list(reversed(meter_readings)),
        meter_chart=_meter_chart(meter_readings),
    )


def _read_equipment_form(form):
    """Only the fixed fields that are always present. The optional
    built-in fields are no longer part of this form — each is its own
    field with its own edit/remove actions (see edit_optional_field)."""
    return {
        "unit_number": form["unit_number"].strip(),
        "equipment_type": form.get("equipment_type", "").strip() or None,
        "make": form["make"].strip(),
        "model": form["model"].strip(),
        "status": form.get("status", "active"),
        "meter_type": form.get("meter_type") if form.get("meter_type") in METER_TYPES else "kilometers",
        "meter_reading": form.get("meter_reading") or 0,
    }


@bp.route("/new", methods=("GET", "POST"))
@login_required
def new():
    if request.method == "POST":
        data = _read_equipment_form(request.form)
        error = None
        if not data["unit_number"] or not data["make"] or not data["model"] or not data["equipment_type"]:
            error = "Unit number, equipment type, make, and model are required."

        if error is None:
            db = get_db()
            try:
                cur = db.execute(
                    """INSERT INTO equipment
                       (unit_number, equipment_type, make, model, meter_type, status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        data["unit_number"], data["equipment_type"], data["make"], data["model"],
                        data["meter_type"], data["status"],
                    ),
                )
                record_meter_reading(
                    db, cur.lastrowid, int(data["meter_reading"] or 0), date.today().isoformat()
                )
                db.commit()
                flash(f"Added {data['unit_number']}.", "success")
                return redirect(url_for("equipment.detail", equipment_id=cur.lastrowid))
            except sqlite3.IntegrityError:
                error = "Equipment with that unit number already exists."

        flash(error, "error")
        return render_template(
            "equipment/form.html", item=data, statuses=STATUSES, custom_fields=[],
            enabled_fields=set(), optional_fields=OPTIONAL_FIELDS, optional_field_labels=OPTIONAL_FIELD_LABELS,
            meter_types=METER_TYPES, fuel_types=FUEL_TYPES, equipment_types=EQUIPMENT_TYPES, mode="new",
        )

    return render_template(
        "equipment/form.html", item={}, statuses=STATUSES, custom_fields=[],
        enabled_fields=set(), optional_fields=OPTIONAL_FIELDS, optional_field_labels=OPTIONAL_FIELD_LABELS,
        meter_types=METER_TYPES, fuel_types=FUEL_TYPES, equipment_types=EQUIPMENT_TYPES, mode="new",
    )


@bp.route("/<int:equipment_id>/edit", methods=("GET", "POST"))
@login_required
def edit(equipment_id):
    item = _equipment_or_404(equipment_id)
    db = get_db()
    custom_fields = _custom_fields_for(db, equipment_id)
    enabled_fields = _enabled_fields_for(db, equipment_id)

    if request.method == "POST":
        data = _read_equipment_form(request.form)
        error = None
        if not data["unit_number"] or not data["make"] or not data["model"] or not data["equipment_type"]:
            error = "Unit number, equipment type, make, and model are required."

        if error is None:
            try:
                db.execute(
                    """UPDATE equipment SET
                        unit_number=?, equipment_type=?, make=?, model=?, meter_type=?, status=?,
                        updated_at=datetime('now')
                       WHERE id=?""",
                    (
                        data["unit_number"], data["equipment_type"], data["make"], data["model"],
                        data["meter_type"], data["status"], equipment_id,
                    ),
                )
                new_reading = int(data["meter_reading"] or 0)
                if new_reading != item["meter_reading"]:
                    record_meter_reading(db, equipment_id, new_reading, date.today().isoformat())
                db.commit()
                flash(f"Updated {data['unit_number']}.", "success")
                return redirect(url_for("equipment.detail", equipment_id=equipment_id))
            except sqlite3.IntegrityError:
                error = "Equipment with that unit number already exists."

        flash(error, "error")
        data["id"] = equipment_id
        return render_template(
            "equipment/form.html", item=data, statuses=STATUSES, custom_fields=custom_fields,
            enabled_fields=enabled_fields, optional_fields=OPTIONAL_FIELDS, optional_field_labels=OPTIONAL_FIELD_LABELS,
            meter_types=METER_TYPES, fuel_types=FUEL_TYPES, equipment_types=EQUIPMENT_TYPES, mode="edit",
        )

    return render_template(
        "equipment/form.html", item=dict(item), statuses=STATUSES, custom_fields=custom_fields,
        enabled_fields=enabled_fields, optional_fields=OPTIONAL_FIELDS, optional_field_labels=OPTIONAL_FIELD_LABELS,
        meter_types=METER_TYPES, fuel_types=FUEL_TYPES, equipment_types=EQUIPMENT_TYPES, mode="edit",
    )


@bp.route("/<int:equipment_id>/delete", methods=("POST",))
@login_required
def delete(equipment_id):
    item = _equipment_or_404(equipment_id)
    db = get_db()
    db.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
    db.commit()
    flash(f"Removed {item['unit_number']}.", "success")
    return redirect(url_for("equipment.list_equipment"))


@bp.route("/<int:equipment_id>/fields/<int:field_id>/edit", methods=("POST",))
@login_required
def edit_field(equipment_id, field_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip() or None

    if label:
        db.execute(
            "UPDATE equipment_custom_fields SET label=?, value=? WHERE id=? AND equipment_id=?",
            (label, value, field_id, equipment_id),
        )
        db.commit()
        flash("Field updated.", "success")
    else:
        flash("A field label is required.", "error")

    return redirect(url_for("equipment.edit", equipment_id=equipment_id))


@bp.route("/<int:equipment_id>/fields/<int:field_id>/delete", methods=("POST",))
@login_required
def delete_field(equipment_id, field_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    db.execute(
        "DELETE FROM equipment_custom_fields WHERE id = ? AND equipment_id = ?",
        (field_id, equipment_id),
    )
    db.commit()
    flash("Field removed.", "success")
    return redirect(url_for("equipment.edit", equipment_id=equipment_id))


@bp.route("/<int:equipment_id>/fields/add", methods=("POST",))
@login_required
def add_field(equipment_id):
    """One combined 'add a field' action: either turns on one of the known
    optional fields, or creates a new custom field — so both live behind a
    single picker in the UI. A typed custom_label always wins over whatever
    the built-in-field dropdown happens to be set to, so it doesn't matter
    whether the user picked "Create new field…" first or just started typing."""
    _equipment_or_404(equipment_id)
    db = get_db()
    field_name = request.form.get("field_name", "")
    custom_label = request.form.get("custom_label", "").strip()

    if custom_label or field_name == "custom":
        label = custom_label
        value = request.form.get("custom_value", "").strip() or None
        if label:
            db.execute(
                "INSERT INTO equipment_custom_fields (equipment_id, label, value) VALUES (?, ?, ?)",
                (equipment_id, label, value),
            )
            db.commit()
            flash(f"Added field {label}.", "success")
        else:
            flash("A name is required for a custom field.", "error")
    elif field_name in OPTIONAL_FIELDS:
        db.execute(
            "INSERT OR IGNORE INTO equipment_enabled_fields (equipment_id, field_name) VALUES (?, ?)",
            (equipment_id, field_name),
        )
        db.commit()
        flash(f"Added {OPTIONAL_FIELD_LABELS[field_name]}.", "success")
    else:
        flash("Choose a field to add.", "error")

    return redirect(url_for("equipment.edit", equipment_id=equipment_id))


@bp.route("/<int:equipment_id>/optional-fields/<field_name>/edit", methods=("POST",))
@login_required
def edit_optional_field(equipment_id, field_name):
    _equipment_or_404(equipment_id)
    if field_name not in OPTIONAL_FIELDS:
        abort(404)
    db = get_db()
    raw = request.form.get("value", "").strip()

    if field_name == "serial_number":
        value = raw.upper() or None
    elif field_name == "fuel_type":
        value = raw if raw in FUEL_TYPES else None
    else:
        value = raw or None

    try:
        db.execute(
            f"UPDATE equipment SET {field_name} = ?, updated_at = datetime('now') WHERE id = ?",
            (value, equipment_id),
        )
        db.commit()
        flash(f"Updated {OPTIONAL_FIELD_LABELS[field_name]}.", "success")
    except sqlite3.IntegrityError:
        flash(f"That {OPTIONAL_FIELD_LABELS[field_name].lower()} is already in use.", "error")

    return redirect(url_for("equipment.edit", equipment_id=equipment_id))


@bp.route("/<int:equipment_id>/optional-fields/<field_name>/remove", methods=("POST",))
@login_required
def remove_optional_field(equipment_id, field_name):
    _equipment_or_404(equipment_id)
    if field_name not in OPTIONAL_FIELDS:
        abort(404)
    db = get_db()
    db.execute(
        "DELETE FROM equipment_enabled_fields WHERE equipment_id = ? AND field_name = ?",
        (equipment_id, field_name),
    )
    db.execute(f"UPDATE equipment SET {field_name} = NULL WHERE id = ?", (equipment_id,))
    db.commit()
    flash(f"Removed {OPTIONAL_FIELD_LABELS[field_name]}.", "success")
    return redirect(url_for("equipment.edit", equipment_id=equipment_id))


@bp.route("/<int:equipment_id>/meter/new", methods=("GET", "POST"))
@login_required
def new_meter_reading(equipment_id):
    item = _equipment_or_404(equipment_id)

    if request.method == "POST":
        reading = request.form.get("reading")
        recorded_at = request.form.get("recorded_at") or date.today().isoformat()
        notes = request.form.get("notes", "").strip() or None
        error = None if reading else "A reading is required."

        if error is None:
            db = get_db()
            record_meter_reading(db, equipment_id, int(reading), recorded_at, notes)
            db.commit()
            flash("Meter reading logged.", "success")
            return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="meter-history"))

        flash(error, "error")
        return render_template(
            "equipment/meter_form.html", item=item,
            reading=reading, recorded_at=recorded_at, notes=notes or "",
        )

    return render_template(
        "equipment/meter_form.html", item=item,
        reading=item["meter_reading"], recorded_at=date.today().isoformat(), notes="",
    )


@bp.route("/<int:equipment_id>/meter/<int:reading_id>/delete", methods=("POST",))
@login_required
def delete_meter_reading(equipment_id, reading_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    db.execute(
        "DELETE FROM equipment_meter_readings WHERE id = ? AND equipment_id = ?",
        (reading_id, equipment_id),
    )
    _resync_meter_reading(db, equipment_id)
    db.commit()
    flash("Meter reading removed.", "success")
    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="meter-history"))


def _parse_quantity(form):
    raw = form.get("quantity", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_quantity_unit(form):
    return form.get("quantity_unit", "").strip() or None


@bp.route("/<int:equipment_id>/info/link", methods=("GET", "POST"))
@login_required
def link_catalog_item(equipment_id):
    item = _equipment_or_404(equipment_id)
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action")
        quantity = _parse_quantity(request.form)
        quantity_unit = _parse_quantity_unit(request.form)

        if action == "link_existing":
            catalog_item_id = request.form.get("catalog_item_id")
            if catalog_item_id:
                db.execute(
                    """INSERT OR IGNORE INTO equipment_catalog_items
                       (equipment_id, catalog_item_id, quantity, quantity_unit) VALUES (?, ?, ?, ?)""",
                    (equipment_id, catalog_item_id, quantity, quantity_unit),
                )
                db.commit()
                flash("Linked.", "success")
                return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))
            flash("Choose an item to link.", "error")

        else:
            category_id = request.form.get("category_id") or None
            new_category_name = request.form.get("new_category_name", "").strip()
            label = request.form.get("label", "").strip()
            value = request.form.get("value", "").strip() or None
            notes = request.form.get("notes", "").strip() or None
            error = None

            if not label:
                error = "Label is required."
            elif not category_id and not new_category_name:
                error = "Choose a category or name a new one."

            if error is None:
                try:
                    if new_category_name:
                        cur = db.execute(
                            "INSERT INTO catalog_categories (name) VALUES (?)", (new_category_name,)
                        )
                        category_id = cur.lastrowid
                    cur = db.execute(
                        "INSERT INTO catalog_items (category_id, label, value, notes) VALUES (?, ?, ?, ?)",
                        (category_id, label, value, notes),
                    )
                    db.execute(
                        """INSERT OR IGNORE INTO equipment_catalog_items
                           (equipment_id, catalog_item_id, quantity, quantity_unit) VALUES (?, ?, ?, ?)""",
                        (equipment_id, cur.lastrowid, quantity, quantity_unit),
                    )
                    db.commit()
                    flash(f"Added {label} to the catalog and linked it.", "success")
                    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))
                except sqlite3.IntegrityError:
                    error = "A category with that name already exists."

            flash(error, "error")

    categories = db.execute("SELECT * FROM catalog_categories ORDER BY sort_order, name").fetchall()
    _cats, _items_by_cat, _alts, linked_ids = get_linked_tree(db, equipment_id)
    all_items = db.execute(
        "SELECT * FROM catalog_items ORDER BY category_id, sort_order, label"
    ).fetchall()
    linkable_by_category = {}
    for row in all_items:
        if row["id"] not in linked_ids:
            linkable_by_category.setdefault(row["category_id"], []).append(row)

    return render_template(
        "equipment/link_catalog_item.html",
        item=item, categories=categories, linkable_by_category=linkable_by_category,
    )


@bp.route("/<int:equipment_id>/info/links/<int:catalog_item_id>/quantity", methods=("POST",))
@login_required
def set_link_quantity(equipment_id, catalog_item_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    quantity = _parse_quantity(request.form)
    quantity_unit = _parse_quantity_unit(request.form)
    db.execute(
        """UPDATE equipment_catalog_items SET quantity = ?, quantity_unit = ?
           WHERE equipment_id = ? AND catalog_item_id = ?""",
        (quantity, quantity_unit, equipment_id, catalog_item_id),
    )
    db.commit()
    flash("Quantity updated.", "success")
    return redirect(url_for(
        "equipment.detail", equipment_id=equipment_id,
        open_item=catalog_item_id, _anchor=f"item-{catalog_item_id}",
    ))


@bp.route("/<int:equipment_id>/info/unlink/<int:catalog_item_id>", methods=("POST",))
@login_required
def unlink_catalog_item(equipment_id, catalog_item_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    db.execute(
        "DELETE FROM equipment_catalog_items WHERE equipment_id = ? AND catalog_item_id = ?",
        (equipment_id, catalog_item_id),
    )
    db.commit()
    flash("Unlinked.", "success")
    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))


@bp.route("/<int:equipment_id>/info-items/new", methods=("POST",))
@login_required
def new_info_item(equipment_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip() or None
    quantity = _parse_quantity(request.form)
    quantity_unit = _parse_quantity_unit(request.form)

    if label:
        db.execute(
            """INSERT INTO equipment_info_items (equipment_id, label, value, quantity, quantity_unit)
               VALUES (?, ?, ?, ?, ?)""",
            (equipment_id, label, value, quantity, quantity_unit),
        )
        db.commit()
        flash(f"Added {label}.", "success")
    else:
        flash("A name is required.", "error")

    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))


@bp.route("/<int:equipment_id>/info-items/<int:item_id>/edit", methods=("POST",))
@login_required
def edit_info_item(equipment_id, item_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip() or None
    quantity = _parse_quantity(request.form)
    quantity_unit = _parse_quantity_unit(request.form)

    if label:
        db.execute(
            """UPDATE equipment_info_items SET label=?, value=?, quantity=?, quantity_unit=?
               WHERE id=? AND equipment_id=?""",
            (label, value, quantity, quantity_unit, item_id, equipment_id),
        )
        db.commit()
        flash("Updated.", "success")
    else:
        flash("A name is required.", "error")

    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))


@bp.route("/<int:equipment_id>/info-items/<int:item_id>/delete", methods=("POST",))
@login_required
def delete_info_item(equipment_id, item_id):
    _equipment_or_404(equipment_id)
    db = get_db()
    db.execute(
        "DELETE FROM equipment_info_items WHERE id = ? AND equipment_id = ?",
        (item_id, equipment_id),
    )
    db.commit()
    flash("Removed.", "success")
    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-info"))
