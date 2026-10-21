import re
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required, permission_required

bp = Blueprint("suppliers", __name__, url_prefix="/suppliers")

# Suggestions only -- supplier_contacts.role is free text, so anything typed
# is kept. Order here is the suggestion order.
CONTACT_ROLES = ["Sales rep", "Parts counter", "Service", "Warranty", "Accounts"]

SHOW_OPTIONS = ("active", "inactive", "all")

SUPPLIER_FIELDS = ("name", "account_number", "phone", "email", "website", "address", "hours", "notes")
CONTACT_FIELDS = ("name", "role", "direct_phone", "cell_phone", "email", "notes")


@bp.app_template_filter("tel_href")
def tel_href(value):
    """A tel: URI for a phone number as typed ("613-555-0142 ext 12",
    "(613) 555.0142"). Keeps a leading + and the digits; anything after an
    extension marker is dropped, since phones handle "tel:...;ext=" so
    inconsistently that dialing the main line is the safer bet."""
    if not value:
        return None
    number = re.split(r"\s*(?:ext\.?|x|#)\s*\d", value, maxsplit=1, flags=re.IGNORECASE)[0]
    digits = re.sub(r"\D", "", number)
    if not digits:
        return None
    return "tel:" + ("+" if number.strip().startswith("+") else "") + digits


@bp.app_template_filter("website_href")
def website_href(value):
    """Websites are usually typed without a scheme ("baldwinfilters.com").
    Anything that isn't already http(s) gets https:// in front, which also
    keeps a typed "javascript:" from ever becoming a live link."""
    if not value:
        return None
    value = value.strip()
    if re.match(r"^https?://", value, re.IGNORECASE):
        return value
    return "https://" + value


def _clean(form, fields):
    return {f: (form.get(f) or "").strip() or None for f in fields}


def _parse_brands(raw):
    """Comma- or newline-separated brand tags, de-duplicated
    case-insensitively, keeping the first spelling typed."""
    seen = {}
    for part in re.split(r"[,\n]", raw or ""):
        brand = part.strip()
        if brand and brand.lower() not in seen:
            seen[brand.lower()] = brand
    return list(seen.values())


def _validate_email(email):
    return email is None or re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) is not None


def _username():
    return g.user["username"] if g.user else None


def _touch(db, supplier_id):
    """Stamp who last changed a supplier (including its contacts)."""
    db.execute(
        "UPDATE suppliers SET updated_at = datetime('now'), updated_by = ? WHERE id = ?",
        (_username(), supplier_id),
    )


def _all_categories(db):
    return db.execute(
        "SELECT * FROM supplier_categories ORDER BY sort_order, name COLLATE NOCASE"
    ).fetchall()


def _supplier_or_404(db, supplier_id):
    supplier = db.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
    if supplier is None:
        abort(404)
    return supplier


def _contact_or_404(db, supplier_id, contact_id):
    contact = db.execute(
        "SELECT * FROM supplier_contacts WHERE id = ? AND supplier_id = ?", (contact_id, supplier_id)
    ).fetchone()
    if contact is None:
        abort(404)
    return contact


def _categories_for(db, supplier_ids):
    """{supplier_id: [category name, ...]} in category-list order."""
    out = {sid: [] for sid in supplier_ids}
    if not supplier_ids:
        return out
    marks = ",".join("?" * len(supplier_ids))
    rows = db.execute(
        f"""SELECT l.supplier_id, c.id, c.name FROM supplier_category_links l
            JOIN supplier_categories c ON c.id = l.category_id
            WHERE l.supplier_id IN ({marks})
            ORDER BY c.sort_order, c.name COLLATE NOCASE""",
        list(supplier_ids),
    ).fetchall()
    for r in rows:
        out[r["supplier_id"]].append(r)
    return out


def _brands_for(db, supplier_ids):
    out = {sid: [] for sid in supplier_ids}
    if not supplier_ids:
        return out
    marks = ",".join("?" * len(supplier_ids))
    rows = db.execute(
        f"""SELECT supplier_id, brand FROM supplier_brands
            WHERE supplier_id IN ({marks}) ORDER BY brand COLLATE NOCASE""",
        list(supplier_ids),
    ).fetchall()
    for r in rows:
        out[r["supplier_id"]].append(r["brand"])
    return out


def _save_links(db, supplier_id, category_ids, brands):
    valid_ids = {r["id"] for r in db.execute("SELECT id FROM supplier_categories")}
    db.execute("DELETE FROM supplier_category_links WHERE supplier_id = ?", (supplier_id,))
    for cid in category_ids:
        if cid in valid_ids:
            db.execute(
                "INSERT OR IGNORE INTO supplier_category_links (supplier_id, category_id) VALUES (?, ?)",
                (supplier_id, cid),
            )
    db.execute("DELETE FROM supplier_brands WHERE supplier_id = ?", (supplier_id,))
    for brand in brands:
        db.execute(
            "INSERT OR IGNORE INTO supplier_brands (supplier_id, brand) VALUES (?, ?)", (supplier_id, brand)
        )


def _read_supplier_form(form):
    data = _clean(form, SUPPLIER_FIELDS)
    category_ids = []
    for raw in form.getlist("category_ids"):
        try:
            category_ids.append(int(raw))
        except ValueError:
            pass
    brands = _parse_brands(form.get("brands"))
    error = None
    if not data["name"]:
        error = "Company name is required."
    elif not _validate_email(data["email"]):
        error = "That email address doesn't look right."
    return data, category_ids, brands, error


@bp.route("/")
@login_required
def list_suppliers():
    db = get_db()
    q = request.args.get("q", "").strip()
    show = request.args.get("show", "active")
    if show not in SHOW_OPTIONS:
        show = "active"
    try:
        category_id = int(request.args.get("category") or 0) or None
    except ValueError:
        category_id = None

    query = "SELECT s.* FROM suppliers s WHERE 1=1"
    params = []
    if show == "active":
        query += " AND s.is_active = 1"
    elif show == "inactive":
        query += " AND s.is_active = 0"
    if category_id:
        query += " AND EXISTS (SELECT 1 FROM supplier_category_links l WHERE l.supplier_id = s.id AND l.category_id = ?)"
        params.append(category_id)
    if q:
        query += """ AND (
            s.name LIKE ?
            OR EXISTS (SELECT 1 FROM supplier_contacts c WHERE c.supplier_id = s.id AND c.name LIKE ?)
            OR EXISTS (SELECT 1 FROM supplier_brands b WHERE b.supplier_id = s.id AND b.brand LIKE ?)
            OR EXISTS (SELECT 1 FROM supplier_category_links l
                       JOIN supplier_categories cat ON cat.id = l.category_id
                       WHERE l.supplier_id = s.id AND cat.name LIKE ?)
        )"""
        params += [f"%{q}%"] * 4
    query += " ORDER BY s.name COLLATE NOCASE, s.id"

    suppliers = db.execute(query, params).fetchall()
    ids = [s["id"] for s in suppliers]
    return render_template(
        "suppliers/list.html",
        suppliers=suppliers,
        categories_by_supplier=_categories_for(db, ids),
        brands_by_supplier=_brands_for(db, ids),
        categories=_all_categories(db),
        q=q, show=show, category_id=category_id,
    )


@bp.route("/<int:supplier_id>")
@login_required
def detail(supplier_id):
    db = get_db()
    supplier = _supplier_or_404(db, supplier_id)
    contacts = db.execute(
        "SELECT * FROM supplier_contacts WHERE supplier_id = ? ORDER BY sort_order, name COLLATE NOCASE",
        (supplier_id,),
    ).fetchall()
    return render_template(
        "suppliers/detail.html",
        supplier=supplier,
        contacts=contacts,
        categories=_categories_for(db, [supplier_id])[supplier_id],
        brands=_brands_for(db, [supplier_id])[supplier_id],
    )


def _render_form(mode, supplier, selected_ids, brands):
    return render_template(
        "suppliers/form.html",
        mode=mode, supplier=supplier, selected_ids=set(selected_ids),
        brands_text=", ".join(brands), categories=_all_categories(get_db()),
    )


@bp.route("/new", methods=("GET", "POST"))
@permission_required("suppliers.edit")
def new():
    if request.method == "POST":
        data, category_ids, brands, error = _read_supplier_form(request.form)
        if error is None:
            db = get_db()
            cur = db.execute(
                """INSERT INTO suppliers
                   (name, account_number, phone, email, website, address, hours, notes,
                    created_by, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*(data[f] for f in SUPPLIER_FIELDS), _username(), _username()),
            )
            _save_links(db, cur.lastrowid, category_ids, brands)
            db.commit()
            flash(f"Added {data['name']}.", "success")
            return redirect(url_for("suppliers.detail", supplier_id=cur.lastrowid))
        flash(error, "error")
        return _render_form("new", data, category_ids, brands)
    return _render_form("new", {}, [], [])


@bp.route("/<int:supplier_id>/edit", methods=("GET", "POST"))
@permission_required("suppliers.edit")
def edit(supplier_id):
    db = get_db()
    supplier = _supplier_or_404(db, supplier_id)
    if request.method == "POST":
        data, category_ids, brands, error = _read_supplier_form(request.form)
        if error is None:
            db.execute(
                """UPDATE suppliers SET
                    name=?, account_number=?, phone=?, email=?, website=?, address=?, hours=?, notes=?
                   WHERE id=?""",
                (*(data[f] for f in SUPPLIER_FIELDS), supplier_id),
            )
            _save_links(db, supplier_id, category_ids, brands)
            _touch(db, supplier_id)
            db.commit()
            flash(f"Updated {data['name']}.", "success")
            return redirect(url_for("suppliers.detail", supplier_id=supplier_id))
        flash(error, "error")
        data["id"] = supplier_id
        data["is_active"] = supplier["is_active"]
        return _render_form("edit", data, category_ids, brands)
    return _render_form(
        "edit", dict(supplier),
        [c["id"] for c in _categories_for(db, [supplier_id])[supplier_id]],
        _brands_for(db, [supplier_id])[supplier_id],
    )


@bp.route("/<int:supplier_id>/deactivate", methods=("POST",))
@permission_required("suppliers.edit")
def deactivate(supplier_id):
    return _set_active(supplier_id, False)


@bp.route("/<int:supplier_id>/reactivate", methods=("POST",))
@permission_required("suppliers.edit")
def reactivate(supplier_id):
    return _set_active(supplier_id, True)


def _set_active(supplier_id, active):
    db = get_db()
    supplier = _supplier_or_404(db, supplier_id)
    db.execute("UPDATE suppliers SET is_active = ? WHERE id = ?", (1 if active else 0, supplier_id))
    _touch(db, supplier_id)
    db.commit()
    flash(f"{supplier['name']} is now {'active' if active else 'inactive'}.", "success")
    return redirect(url_for("suppliers.detail", supplier_id=supplier_id))


def _read_contact_form(form):
    data = _clean(form, CONTACT_FIELDS)
    error = None
    if not data["name"]:
        error = "Contact name is required."
    elif not _validate_email(data["email"]):
        error = "That email address doesn't look right."
    return data, error


@bp.route("/<int:supplier_id>/contacts/new", methods=("GET", "POST"))
@permission_required("suppliers.edit")
def new_contact(supplier_id):
    db = get_db()
    supplier = _supplier_or_404(db, supplier_id)
    contact = {}
    if request.method == "POST":
        contact, error = _read_contact_form(request.form)
        if error is None:
            db.execute(
                """INSERT INTO supplier_contacts
                   (supplier_id, name, role, direct_phone, cell_phone, email, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (supplier_id, *(contact[f] for f in CONTACT_FIELDS)),
            )
            _touch(db, supplier_id)
            db.commit()
            flash(f"Added {contact['name']}.", "success")
            return redirect(url_for("suppliers.detail", supplier_id=supplier_id, _anchor="contacts"))
        flash(error, "error")
    return render_template(
        "suppliers/contact_form.html", mode="new", supplier=supplier, contact=contact, roles=CONTACT_ROLES,
    )


@bp.route("/<int:supplier_id>/contacts/<int:contact_id>/edit", methods=("GET", "POST"))
@permission_required("suppliers.edit")
def edit_contact(supplier_id, contact_id):
    db = get_db()
    supplier = _supplier_or_404(db, supplier_id)
    contact = dict(_contact_or_404(db, supplier_id, contact_id))
    if request.method == "POST":
        data, error = _read_contact_form(request.form)
        if error is None:
            db.execute(
                """UPDATE supplier_contacts SET
                    name=?, role=?, direct_phone=?, cell_phone=?, email=?, notes=?
                   WHERE id=?""",
                (*(data[f] for f in CONTACT_FIELDS), contact_id),
            )
            _touch(db, supplier_id)
            db.commit()
            flash(f"Updated {data['name']}.", "success")
            return redirect(url_for("suppliers.detail", supplier_id=supplier_id, _anchor="contacts"))
        flash(error, "error")
        contact = {**data, "id": contact_id}
    return render_template(
        "suppliers/contact_form.html", mode="edit", supplier=supplier, contact=contact, roles=CONTACT_ROLES,
    )


@bp.route("/<int:supplier_id>/contacts/<int:contact_id>/delete", methods=("POST",))
@permission_required("suppliers.edit")
def delete_contact(supplier_id, contact_id):
    """Contacts can be removed outright (people move on); only the supplier
    itself is soft-deleted."""
    db = get_db()
    _supplier_or_404(db, supplier_id)
    contact = _contact_or_404(db, supplier_id, contact_id)
    db.execute("DELETE FROM supplier_contacts WHERE id = ?", (contact_id,))
    _touch(db, supplier_id)
    db.commit()
    flash(f"Removed {contact['name']}.", "success")
    return redirect(url_for("suppliers.detail", supplier_id=supplier_id, _anchor="contacts"))


@bp.route("/categories")
@login_required
def categories():
    db = get_db()
    rows = db.execute(
        """SELECT c.*, (SELECT COUNT(*) FROM supplier_category_links l WHERE l.category_id = c.id) AS supplier_count
           FROM supplier_categories c ORDER BY c.sort_order, c.name COLLATE NOCASE"""
    ).fetchall()
    return render_template("suppliers/categories.html", categories=rows)


@bp.route("/categories/new", methods=("POST",))
@permission_required("suppliers.edit")
def new_category():
    db = get_db()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("suppliers.categories"))
    next_order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM supplier_categories").fetchone()["n"]
    try:
        db.execute("INSERT INTO supplier_categories (name, sort_order) VALUES (?, ?)", (name, next_order))
        db.commit()
        flash(f"Added category {name}.", "success")
    except sqlite3.IntegrityError:
        flash(f"There's already a category called {name}.", "error")
    return redirect(url_for("suppliers.categories"))


@bp.route("/categories/<int:category_id>/edit", methods=("POST",))
@permission_required("suppliers.edit")
def edit_category(category_id):
    db = get_db()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("suppliers.categories"))
    try:
        cur = db.execute("UPDATE supplier_categories SET name = ? WHERE id = ?", (name, category_id))
        if cur.rowcount == 0:
            abort(404)
        db.commit()
        flash(f"Renamed category to {name}.", "success")
    except sqlite3.IntegrityError:
        flash(f"There's already a category called {name}.", "error")
    return redirect(url_for("suppliers.categories"))


@bp.route("/categories/<int:category_id>/delete", methods=("POST",))
@permission_required("suppliers.edit")
def delete_category(category_id):
    """Removes the category and its tags on suppliers; the suppliers
    themselves are untouched."""
    db = get_db()
    category = db.execute("SELECT * FROM supplier_categories WHERE id = ?", (category_id,)).fetchone()
    if category is None:
        abort(404)
    db.execute("DELETE FROM supplier_categories WHERE id = ?", (category_id,))
    db.commit()
    flash(f"Removed category {category['name']}.", "success")
    return redirect(url_for("suppliers.categories"))
