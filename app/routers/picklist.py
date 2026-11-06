from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required

bp = Blueprint("picklist", __name__, url_prefix="/pick-list")

MAX_QUANTITY = 999


@bp.app_context_processor
def inject_pick_list_count():
    """Number of parts on the signed-in user's list, for the nav badge."""
    if getattr(g, "user", None) is None:
        return {"pick_list_count": 0}
    count = get_db().execute(
        "SELECT COUNT(*) AS n FROM pick_list_items WHERE user_id = ?", (g.user["id"],)
    ).fetchone()["n"]
    return {"pick_list_count": count}


def _wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


def _parse_quantity(raw, default=1):
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(quantity, MAX_QUANTITY))


def _back(default_endpoint="picklist.index"):
    """Return to the page the button was on (a local path only)."""
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for(default_endpoint))


def _count(db):
    return db.execute(
        "SELECT COUNT(*) AS n FROM pick_list_items WHERE user_id = ?", (g.user["id"],)
    ).fetchone()["n"]


def add_to_list(db, catalog_item_id, quantity):
    """Add `quantity` of a part to the signed-in user's list, on top of any
    already there."""
    db.execute(
        """INSERT INTO pick_list_items (user_id, catalog_item_id, quantity) VALUES (?, ?, ?)
           ON CONFLICT (user_id, catalog_item_id)
           DO UPDATE SET quantity = MIN(quantity + excluded.quantity, ?)""",
        (g.user["id"], catalog_item_id, quantity, MAX_QUANTITY),
    )


def _rows(db):
    """The list in walking order: warehouse positions first (by their
    sortable code), then free-text locations, then parts with no location."""
    return db.execute(
        """SELECT p.catalog_item_id, p.quantity,
                  COALESCE(NULLIF(c.value, ''), c.label) AS part_number,
                  c.label, c.location, c.location_code, c.quantity_on_hand,
                  cat.name AS type_name
           FROM pick_list_items p
           JOIN catalog_items c ON c.id = p.catalog_item_id
           JOIN catalog_categories cat ON cat.id = c.category_id
           WHERE p.user_id = ?
           ORDER BY c.location_code IS NULL, c.location_code,
                    c.location IS NULL OR c.location = '', c.location COLLATE NOCASE,
                    part_number COLLATE NOCASE""",
        (g.user["id"],),
    ).fetchall()


@bp.route("/")
@login_required
def index():
    db = get_db()
    return render_template("picklist/index.html", rows=_rows(db))


@bp.route("/add", methods=("POST",))
@login_required
def add():
    db = get_db()
    try:
        item_id = int(request.form.get("item_id", ""))
    except ValueError:
        abort(400)
    item = db.execute(
        "SELECT id, COALESCE(NULLIF(value, ''), label) AS part_number FROM catalog_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    if item is None:
        abort(404)
    add_to_list(db, item_id, _parse_quantity(request.form.get("quantity")))
    db.commit()
    if _wants_json():
        return {"ok": True, "count": _count(db)}
    flash(f"Added {item['part_number']} to your pick list.", "success")
    return _back()


@bp.route("/add-unit/<int:equipment_id>", methods=("POST",))
@login_required
def add_unit(equipment_id):
    """Every part linked to a unit, in the quantity that unit takes -- e.g.
    everything needed for a service. Quantities with a unit of measure
    (5.7 liters of oil) aren't part counts, so those go on as 1."""
    db = get_db()
    unit = db.execute("SELECT id, unit_number FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if unit is None:
        abort(404)
    links = db.execute(
        "SELECT catalog_item_id, quantity, quantity_unit FROM equipment_catalog_items WHERE equipment_id = ?",
        (equipment_id,),
    ).fetchall()
    for link in links:
        if link["quantity"] and not link["quantity_unit"] and float(link["quantity"]).is_integer():
            quantity = _parse_quantity(int(link["quantity"]))
        else:
            quantity = 1
        add_to_list(db, link["catalog_item_id"], quantity)
    db.commit()
    if links:
        flash(f"Added {len(links)} part{'' if len(links) == 1 else 's'} for {unit['unit_number']} to your pick list.", "success")
    else:
        flash(f"{unit['unit_number']} has no linked parts to add.", "error")
    return _back()


@bp.route("/items/<int:item_id>/quantity", methods=("POST",))
@login_required
def set_quantity(item_id):
    db = get_db()
    quantity = _parse_quantity(request.form.get("quantity"))
    db.execute(
        "UPDATE pick_list_items SET quantity = ? WHERE user_id = ? AND catalog_item_id = ?",
        (quantity, g.user["id"], item_id),
    )
    db.commit()
    if _wants_json():
        return {"ok": True, "quantity": quantity}
    return redirect(url_for("picklist.index"))


@bp.route("/items/<int:item_id>/remove", methods=("POST",))
@login_required
def remove(item_id):
    db = get_db()
    db.execute(
        "DELETE FROM pick_list_items WHERE user_id = ? AND catalog_item_id = ?", (g.user["id"], item_id)
    )
    db.commit()
    return redirect(url_for("picklist.index"))


@bp.route("/clear", methods=("POST",))
@login_required
def clear():
    """Empty the list. With remove_from_inventory ticked, first subtract each
    quantity from the part's count on hand -- the "I pulled these off the
    shelf" step. Parts that were never counted are left uncounted rather
    than guessed at, and a count never goes below zero."""
    db = get_db()
    rows = _rows(db)
    if not rows:
        return redirect(url_for("picklist.index"))

    if request.form.get("remove_from_inventory"):
        if "inventory.edit" not in g.perms:
            flash("You don't have permission to change stock counts, so nothing was removed from inventory "
                  "and your list was kept.", "error")
            return redirect(url_for("picklist.index"))
        uncounted, short = [], []
        for row in rows:
            on_hand = row["quantity_on_hand"]
            if on_hand is None:
                uncounted.append(row["part_number"])
                continue
            if row["quantity"] > on_hand:
                short.append(row["part_number"])
            db.execute(
                "UPDATE catalog_items SET quantity_on_hand = ? WHERE id = ?",
                (max(on_hand - row["quantity"], 0), row["catalog_item_id"]),
            )
        removed = len(rows) - len(uncounted)
        message = f"Removed {removed} part{'' if removed == 1 else 's'} from inventory and cleared your list."
        if short:
            message += f" More were picked than on hand for {', '.join(short)}; set to 0 -- worth a recount."
        if uncounted:
            message += f" No count on file for {', '.join(uncounted)}, so left as is."
        flash(message, "success")
    else:
        flash("Cleared your pick list.", "success")

    db.execute("DELETE FROM pick_list_items WHERE user_id = ?", (g.user["id"],))
    db.commit()
    return redirect(url_for("picklist.index"))
