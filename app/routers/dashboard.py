from datetime import date, timedelta

from flask import Blueprint, render_template

from ..db import get_db
from .auth import login_required

bp = Blueprint("dashboard", __name__)

DUE_SOON_DAYS = 14
DUE_SOON_MILEAGE = 500


def _status_for(vehicle_row, upcoming_by_vehicle):
    """Return 'overdue', 'due_soon', or 'ok' for a vehicle based on its
    nearest upcoming maintenance threshold."""
    upcoming = upcoming_by_vehicle.get(vehicle_row["id"])
    if not upcoming:
        return "ok"

    today = date.today()
    overdue = False
    due_soon = False

    if upcoming["next_due_date"]:
        due = date.fromisoformat(upcoming["next_due_date"])
        if due < today:
            overdue = True
        elif due <= today + timedelta(days=DUE_SOON_DAYS):
            due_soon = True

    if upcoming["next_due_mileage"]:
        remaining = upcoming["next_due_mileage"] - vehicle_row["mileage"]
        if remaining < 0:
            overdue = True
        elif remaining <= DUE_SOON_MILEAGE:
            due_soon = True

    if overdue:
        return "overdue"
    if due_soon:
        return "due_soon"
    return "ok"


@bp.route("/")
@login_required
def index():
    db = get_db()

    vehicles = db.execute(
        "SELECT * FROM vehicles WHERE status != 'retired' ORDER BY unit_number"
    ).fetchall()

    # Nearest (soonest) upcoming due date/mileage per vehicle, taken from
    # the most recently logged service record that has a next-due value set.
    upcoming_rows = db.execute(
        """
        SELECT vehicle_id, next_due_date, next_due_mileage
        FROM maintenance_records
        WHERE next_due_date IS NOT NULL OR next_due_mileage IS NOT NULL
        ORDER BY service_date DESC
        """
    ).fetchall()

    upcoming_by_vehicle = {}
    for row in upcoming_rows:
        upcoming_by_vehicle.setdefault(row["vehicle_id"], row)

    counts = {"active": 0, "overdue": 0, "due_soon": 0}
    attention = []

    for v in vehicles:
        if v["status"] == "active":
            counts["active"] += 1
        vstatus = _status_for(v, upcoming_by_vehicle)
        if vstatus in ("overdue", "due_soon"):
            counts[vstatus] += 1
            attention.append((v, vstatus, upcoming_by_vehicle.get(v["id"])))

    order = {"overdue": 0, "due_soon": 1}
    attention.sort(key=lambda item: order[item[1]])

    return render_template(
        "dashboard/index.html",
        total_vehicles=len(vehicles),
        counts=counts,
        attention=attention,
    )
