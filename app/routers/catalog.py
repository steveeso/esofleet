import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required

bp = Blueprint("catalog", __name__, url_prefix="/catalog")


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
    """Return (categories, items_by_category, alternates_by_item, linked_ids)
    for the catalog items one equipment item is linked to, grouped by
    category — used to render an equipment item's Maintenance Info section."""
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
        return [], {}, {}, set()

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

    return categories, items_by_category, alternates_by_item, {row["id"] for row in items}


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

    return render_template(
        "catalog/index.html",
        categories=categories,
        items_by_category=items_by_category,
        alternates_by_item=alternates_by_item,
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
                "INSERT INTO catalog_items (category_id, label, value, notes) VALUES (?, ?, ?, ?)",
                (category_id, data["label"], data["value"], data["notes"]),
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
                "UPDATE catalog_items SET label=?, value=?, notes=? WHERE id=?",
                (data["label"], data["value"], data["notes"], item_id),
            )
            db.commit()
            flash("Updated.", "success")
            return redirect(next_url) if next_url else redirect(
                url_for("catalog.index", open=category["id"], open_item=item_id, _anchor=f"item-{item_id}")
            )

        flash(error, "error")
        data["id"] = item_id
        return render_template(
            "catalog/item_form.html", category=category, item=data, mode="edit", next=next_url
        )

    return render_template(
        "catalog/item_form.html", category=category, item=dict(item), mode="edit", next=next_url
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
