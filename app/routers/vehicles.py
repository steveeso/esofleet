import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import get_db
from .auth import login_required

bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

STATUSES = ["active", "in_shop", "retired"]


@bp.route("/")
@login_required
def list_vehicles():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")

    query = "SELECT * FROM vehicles WHERE 1=1"
    params = []

    if q:
        query += """ AND (
            unit_number LIKE ? OR make LIKE ? OR model LIKE ?
            OR vin LIKE ? OR license_plate LIKE ? OR assigned_driver LIKE ?
        )"""
        like = f"%{q}%"
        params += [like] * 6

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY unit_number"

    vehicles = db.execute(query, params).fetchall()

    return render_template(
        "vehicles/list.html",
        vehicles=vehicles,
        q=q,
        status=status,
        statuses=STATUSES,
    )


def _vehicle_or_404(vehicle_id):
    db = get_db()
    vehicle = db.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if vehicle is None:
        abort(404)
    return vehicle


@bp.route("/<int:vehicle_id>")
@login_required
def detail(vehicle_id):
    vehicle = _vehicle_or_404(vehicle_id)
    db = get_db()
    records = db.execute(
        """SELECT * FROM maintenance_records
           WHERE vehicle_id = ?
           ORDER BY service_date DESC, id DESC""",
        (vehicle_id,),
    ).fetchall()
    return render_template("vehicles/detail.html", vehicle=vehicle, records=records)


def _read_vehicle_form(form):
    return {
        "unit_number": form["unit_number"].strip(),
        "make": form["make"].strip(),
        "model": form["model"].strip(),
        "year": form.get("year") or None,
        "vin": form.get("vin", "").strip().upper() or None,
        "license_plate": form.get("license_plate", "").strip() or None,
        "mileage": form.get("mileage") or 0,
        "status": form.get("status", "active"),
        "assigned_driver": form.get("assigned_driver", "").strip() or None,
        "notes": form.get("notes", "").strip() or None,
    }


@bp.route("/new", methods=("GET", "POST"))
@login_required
def new():
    if request.method == "POST":
        data = _read_vehicle_form(request.form)
        error = None
        if not data["unit_number"] or not data["make"] or not data["model"]:
            error = "Unit number, make, and model are required."

        if error is None:
            db = get_db()
            try:
                cur = db.execute(
                    """INSERT INTO vehicles
                       (unit_number, make, model, year, vin, license_plate,
                        mileage, status, assigned_driver, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        data["unit_number"], data["make"], data["model"],
                        data["year"], data["vin"], data["license_plate"],
                        data["mileage"], data["status"], data["assigned_driver"],
                        data["notes"],
                    ),
                )
                db.commit()
                flash(f"Added vehicle {data['unit_number']}.", "success")
                return redirect(url_for("vehicles.detail", vehicle_id=cur.lastrowid))
            except sqlite3.IntegrityError:
                error = "A vehicle with that unit number or VIN already exists."

        flash(error, "error")
        return render_template("vehicles/form.html", vehicle=data, statuses=STATUSES, mode="new")

    return render_template("vehicles/form.html", vehicle={}, statuses=STATUSES, mode="new")


@bp.route("/<int:vehicle_id>/edit", methods=("GET", "POST"))
@login_required
def edit(vehicle_id):
    vehicle = _vehicle_or_404(vehicle_id)

    if request.method == "POST":
        data = _read_vehicle_form(request.form)
        error = None
        if not data["unit_number"] or not data["make"] or not data["model"]:
            error = "Unit number, make, and model are required."

        if error is None:
            db = get_db()
            try:
                db.execute(
                    """UPDATE vehicles SET
                        unit_number=?, make=?, model=?, year=?, vin=?,
                        license_plate=?, mileage=?, status=?, assigned_driver=?,
                        notes=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (
                        data["unit_number"], data["make"], data["model"],
                        data["year"], data["vin"], data["license_plate"],
                        data["mileage"], data["status"], data["assigned_driver"],
                        data["notes"], vehicle_id,
                    ),
                )
                db.commit()
                flash(f"Updated vehicle {data['unit_number']}.", "success")
                return redirect(url_for("vehicles.detail", vehicle_id=vehicle_id))
            except sqlite3.IntegrityError:
                error = "A vehicle with that unit number or VIN already exists."

        flash(error, "error")
        data["id"] = vehicle_id
        return render_template("vehicles/form.html", vehicle=data, statuses=STATUSES, mode="edit")

    return render_template("vehicles/form.html", vehicle=dict(vehicle), statuses=STATUSES, mode="edit")


@bp.route("/<int:vehicle_id>/delete", methods=("POST",))
@login_required
def delete(vehicle_id):
    vehicle = _vehicle_or_404(vehicle_id)
    db = get_db()
    db.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    db.commit()
    flash(f"Removed vehicle {vehicle['unit_number']}.", "success")
    return redirect(url_for("vehicles.list_vehicles"))
