from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required

bp = Blueprint("maintenance", __name__, url_prefix="/vehicles/<int:vehicle_id>/maintenance")


def _vehicle_or_404(db, vehicle_id):
    vehicle = db.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if vehicle is None:
        abort(404)
    return vehicle


def _read_form(form):
    return {
        "service_date": form["service_date"],
        "service_type": form["service_type"].strip(),
        "description": form.get("description", "").strip() or None,
        "cost": form.get("cost") or None,
        "mileage_at_service": form.get("mileage_at_service") or None,
        "next_due_date": form.get("next_due_date") or None,
        "next_due_mileage": form.get("next_due_mileage") or None,
        "performed_by": form.get("performed_by", "").strip() or None,
    }


@bp.route("/new", methods=("GET", "POST"))
@login_required
def new(vehicle_id):
    db = get_db()
    vehicle = _vehicle_or_404(db, vehicle_id)

    if request.method == "POST":
        data = _read_form(request.form)
        error = None
        if not data["service_date"] or not data["service_type"]:
            error = "Service date and service type are required."

        if error is None:
            db.execute(
                """INSERT INTO maintenance_records
                   (vehicle_id, service_date, service_type, description, cost,
                    mileage_at_service, next_due_date, next_due_mileage, performed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vehicle_id, data["service_date"], data["service_type"],
                    data["description"], data["cost"], data["mileage_at_service"],
                    data["next_due_date"], data["next_due_mileage"], data["performed_by"],
                ),
            )
            # Keep the vehicle's odometer current if this service logged a
            # higher reading than what's on file.
            if data["mileage_at_service"]:
                db.execute(
                    """UPDATE vehicles SET mileage = MAX(mileage, ?),
                       updated_at = datetime('now') WHERE id = ?""",
                    (int(data["mileage_at_service"]), vehicle_id),
                )
            db.commit()
            flash("Maintenance record added.", "success")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle_id))

        flash(error, "error")
        return render_template("maintenance/form.html", vehicle=vehicle, record=data, mode="new")

    return render_template("maintenance/form.html", vehicle=vehicle, record={}, mode="new")


@bp.route("/<int:record_id>/edit", methods=("GET", "POST"))
@login_required
def edit(vehicle_id, record_id):
    db = get_db()
    vehicle = _vehicle_or_404(db, vehicle_id)
    record = db.execute(
        "SELECT * FROM maintenance_records WHERE id = ? AND vehicle_id = ?",
        (record_id, vehicle_id),
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
                    mileage_at_service=?, next_due_date=?, next_due_mileage=?,
                    performed_by=?
                   WHERE id=?""",
                (
                    data["service_date"], data["service_type"], data["description"],
                    data["cost"], data["mileage_at_service"], data["next_due_date"],
                    data["next_due_mileage"], data["performed_by"], record_id,
                ),
            )
            db.commit()
            flash("Maintenance record updated.", "success")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle_id))

        flash(error, "error")
        data["id"] = record_id
        return render_template("maintenance/form.html", vehicle=vehicle, record=data, mode="edit")

    return render_template("maintenance/form.html", vehicle=vehicle, record=dict(record), mode="edit")


@bp.route("/<int:record_id>/delete", methods=("POST",))
@login_required
def delete(vehicle_id, record_id):
    db = get_db()
    _vehicle_or_404(db, vehicle_id)
    db.execute(
        "DELETE FROM maintenance_records WHERE id = ? AND vehicle_id = ?",
        (record_id, vehicle_id),
    )
    db.commit()
    flash("Maintenance record deleted.", "success")
    return redirect(url_for("vehicles.detail", vehicle_id=vehicle_id))
