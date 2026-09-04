from datetime import date, timedelta

from flask import Blueprint, render_template, request

from ..db import get_db
from .auth import login_required

bp = Blueprint("dashboard", __name__)

# Scheduled-maintenance thresholds — not currently surfaced anywhere (that
# feature is on hold), kept here so it's ready to wire back in later.
DUE_SOON_DAYS = 14
DUE_SOON_METER = {"kilometers": 800, "hours": 20, "miles": 500}


def _status_for(item, upcoming_by_equipment):
    """Return 'overdue', 'due_soon', or 'ok' for a piece of equipment based on
    its nearest upcoming maintenance threshold. Unused for now — see above."""
    upcoming = upcoming_by_equipment.get(item["id"])
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

    if upcoming["next_due_meter_reading"]:
        remaining = upcoming["next_due_meter_reading"] - item["meter_reading"]
        due_soon_meter = DUE_SOON_METER.get(item["meter_type"], DUE_SOON_METER["kilometers"])
        if remaining < 0:
            overdue = True
        elif remaining <= due_soon_meter:
            due_soon = True

    if overdue:
        return "overdue"
    if due_soon:
        return "due_soon"
    return "ok"


# Maps a sort key from the query string to a safe ORDER BY expression.
# Whitelisted so the value can be interpolated directly into SQL.
SORT_COLUMNS = {
    "unit": "unit_number",
    "type": "equipment_type IS NULL, equipment_type",
    "equipment": "make, model, year",
    "fuel": "fuel_type IS NULL, fuel_type",
    "meter": "meter_reading",
    "status": "status",
}
DEFAULT_SORT = "unit"


@bp.route("/")
@login_required
def index():
    db = get_db()

    sort = request.args.get("sort", DEFAULT_SORT)
    if sort not in SORT_COLUMNS:
        sort = DEFAULT_SORT
    direction = request.args.get("dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"

    items = db.execute(
        f"SELECT * FROM equipment ORDER BY {SORT_COLUMNS[sort]} {direction.upper()}"
    ).fetchall()

    counts = {"active": 0, "in_shop": 0, "retired": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    return render_template(
        "dashboard/index.html",
        total_equipment=len(items),
        counts=counts,
        items=items,
        sort=sort,
        dir=direction,
    )
