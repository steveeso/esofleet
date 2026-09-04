from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required
from .equipment import record_meter_reading

bp = Blueprint("maintenance", __name__, url_prefix="/equipment/<int:equipment_id>/maintenance")


def _equipment_or_404(db, equipment_id):
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def _read_form(form):
    return {
        "service_date": form["service_date"],
        "service_type": form["service_type"].strip(),
        "description": form.get("description", "").strip() or None,
        "cost": form.get("cost") or None,
        "meter_reading_at_service": form.get("meter_reading_at_service") or None,
        "next_due_date": form.get("next_due_date") or None,
        "next_due_meter_reading": form.get("next_due_meter_reading") or None,
        "performed_by": form.get("performed_by", "").strip() or None,
    }


@bp.route("/new", methods=("GET", "POST"))
@login_required
def new(equipment_id):
    db = get_db()
    item = _equipment_or_404(db, equipment_id)

    if request.method == "POST":
        data = _read_form(request.form)
        error = None
        if not data["service_date"] or not data["service_type"]:
            error = "Service date and service type are required."

        if error is None:
            db.execute(
                """INSERT INTO maintenance_records
                   (equipment_id, service_date, service_type, description, cost,
                    meter_reading_at_service, next_due_date, next_due_meter_reading, performed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    equipment_id, data["service_date"], data["service_type"],
                    data["description"], data["cost"], data["meter_reading_at_service"],
                    data["next_due_date"], data["next_due_meter_reading"], data["performed_by"],
                ),
            )
            # Feed this service's reading into the equipment's meter history.
            if data["meter_reading_at_service"]:
                record_meter_reading(
                    db, equipment_id, int(data["meter_reading_at_service"]), data["service_date"],
                )
            db.commit()
            flash("Maintenance record added.", "success")
            return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-history"))

        flash(error, "error")
        return render_template("maintenance/form.html", item=item, record=data, mode="new")

    return render_template("maintenance/form.html", item=item, record={}, mode="new")


@bp.route("/<int:record_id>/edit", methods=("GET", "POST"))
@login_required
def edit(equipment_id, record_id):
    db = get_db()
    item = _equipment_or_404(db, equipment_id)
    record = db.execute(
        "SELECT * FROM maintenance_records WHERE id = ? AND equipment_id = ?",
        (record_id, equipment_id),
    ).fetchone()
    if record is None:
        abort(404)

    if request.method == "POST":
        data = _read_form(request.form)
        error = None
        if not data["service_date"] or not data["service_type"]:
            error = "Service date and service type are required."

        if error is None:
            db.execute(
                """UPDATE maintenance_records SET
                    service_date=?, service_type=?, description=?, cost=?,
                    meter_reading_at_service=?, next_due_date=?, next_due_meter_reading=?,
                    performed_by=?
                   WHERE id=?""",
                (
                    data["service_date"], data["service_type"], data["description"],
                    data["cost"], data["meter_reading_at_service"], data["next_due_date"],
                    data["next_due_meter_reading"], data["performed_by"], record_id,
                ),
            )
            if data["meter_reading_at_service"]:
                record_meter_reading(
                    db, equipment_id, int(data["meter_reading_at_service"]), data["service_date"],
                )
            db.commit()
            flash("Maintenance record updated.", "success")
            return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-history"))

        flash(error, "error")
        data["id"] = record_id
        return render_template("maintenance/form.html", item=item, record=data, mode="edit")

    return render_template("maintenance/form.html", item=item, record=dict(record), mode="edit")


@bp.route("/<int:record_id>/delete", methods=("POST",))
@login_required
def delete(equipment_id, record_id):
    db = get_db()
    _equipment_or_404(db, equipment_id)
    db.execute(
        "DELETE FROM maintenance_records WHERE id = ? AND equipment_id = ?",
        (record_id, equipment_id),
    )
    db.commit()
    flash("Maintenance record deleted.", "success")
    return redirect(url_for("equipment.detail", equipment_id=equipment_id, _anchor="maintenance-history"))
