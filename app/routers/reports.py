from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import permissions
from ..db import get_db
from .auth import admin_required, login_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

# (key, label). Order here is the form's order.
KINDS = [
    ("problem", "Something's wrong"),
    ("reorder", "Order more"),
    ("other", "Other"),
]
KIND_LABELS = dict(KINDS)

STATUSES = ("open", "resolved")


def _is_admin():
    return g.user is not None and g.user["role"] == permissions.ADMIN_ROLE


@bp.app_context_processor
def inject_open_report_count():
    """Open reports, for the admin's nav badge."""
    if not _is_admin():
        return {"open_report_count": 0}
    count = get_db().execute("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'").fetchone()["n"]
    return {"open_report_count": count}


def _int_arg(source, key):
    try:
        return int(source.get(key) or 0) or None
    except ValueError:
        return None


def _subject(db, item_id, equipment_id):
    """The part and/or unit a report is about, if they (still) exist."""
    item = unit = None
    if item_id:
        item = db.execute(
            "SELECT id, label, COALESCE(NULLIF(value, ''), label) AS part_number FROM catalog_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    if equipment_id:
        unit = db.execute(
            "SELECT id, unit_number, make, model FROM equipment WHERE id = ?", (equipment_id,)
        ).fetchone()
    return item, unit


def _report_rows(db, where, params):
    return db.execute(
        f"""SELECT r.*, COALESCE(NULLIF(c.value, ''), c.label) AS part_number, e.unit_number
            FROM reports r
            LEFT JOIN catalog_items c ON c.id = r.catalog_item_id
            LEFT JOIN equipment e ON e.id = r.equipment_id
            WHERE {where}
            ORDER BY r.created_at DESC, r.id DESC""",
        params,
    ).fetchall()


@bp.route("/")
@login_required
def index():
    """Admins see everyone's reports (open ones by default); everyone else
    sees just the ones they sent, so they can tell when one's been dealt
    with."""
    db = get_db()
    status = request.args.get("status", "open")
    if status not in STATUSES:
        status = "open"
    if _is_admin():
        rows = _report_rows(db, "r.status = ?", (status,))
    else:
        rows = _report_rows(db, "r.created_by_id = ? AND r.status = ?", (g.user["id"], status))
    counts = {
        s: db.execute(
            "SELECT COUNT(*) AS n FROM reports WHERE status = ?" + ("" if _is_admin() else " AND created_by_id = ?"),
            (s,) if _is_admin() else (s, g.user["id"]),
        ).fetchone()["n"]
        for s in STATUSES
    }
    return render_template(
        "reports/index.html", rows=rows, status=status, counts=counts,
        kind_labels=KIND_LABELS, is_admin=_is_admin(),
    )


@bp.route("/new", methods=("GET", "POST"))
@login_required
def new():
    db = get_db()
    source = request.form if request.method == "POST" else request.args
    item_id, equipment_id = _int_arg(source, "item"), _int_arg(source, "equipment")
    item, unit = _subject(db, item_id, equipment_id)
    kind = source.get("kind") if source.get("kind") in KIND_LABELS else "problem"
    message = (request.form.get("message") or "").strip()

    if request.method == "POST":
        if not message:
            flash("Say what the problem is.", "error")
        else:
            db.execute(
                """INSERT INTO reports (kind, message, catalog_item_id, equipment_id, created_by_id, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (kind, message, item["id"] if item else None, unit["id"] if unit else None,
                 g.user["id"], g.user["username"]),
            )
            db.commit()
            flash("Thanks -- sent to the admins.", "success")
            if item:
                return redirect(url_for("catalog.item_detail", item_id=item["id"]))
            if unit:
                return redirect(url_for("equipment.detail", equipment_id=unit["id"]))
            return redirect(url_for("reports.index"))

    return render_template(
        "reports/form.html", item=item, unit=unit, kind=kind, message=message, kinds=KINDS,
    )


def _report_or_404(db, report_id):
    report = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        abort(404)
    return report


@bp.route("/<int:report_id>/resolve", methods=("POST",))
@admin_required
def resolve(report_id):
    db = get_db()
    _report_or_404(db, report_id)
    reply = (request.form.get("admin_reply") or "").strip() or None
    db.execute(
        """UPDATE reports SET status = 'resolved', resolved_by = ?, resolved_at = datetime('now'),
               admin_reply = ? WHERE id = ?""",
        (g.user["username"], reply, report_id),
    )
    db.commit()
    flash("Marked resolved.", "success")
    return redirect(url_for("reports.index"))


@bp.route("/<int:report_id>/reopen", methods=("POST",))
@admin_required
def reopen(report_id):
    db = get_db()
    _report_or_404(db, report_id)
    db.execute(
        "UPDATE reports SET status = 'open', resolved_by = NULL, resolved_at = NULL WHERE id = ?", (report_id,)
    )
    db.commit()
    flash("Reopened.", "success")
    return redirect(url_for("reports.index", status="resolved"))
