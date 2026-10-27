from datetime import date, timedelta

from flask import Blueprint, render_template, request

from ..db import get_db
from .auth import login_required
from .equipment import FLEET_STATUSES, RETIRED

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

    all_items = db.execute(
        f"SELECT * FROM equipment WHERE status != ? ORDER BY {SORT_COLUMNS[sort]} {direction.upper()}",
        (RETIRED,),
    ).fetchall()

    # Tab counts always reflect the whole fleet, regardless of which tab is
    # currently selected, so switching tabs doesn't make the other tabs'
    # own counts appear to change.
    type_counts = {}
    for item in all_items:
        t = item["equipment_type"] or "Uncategorized"
        type_counts[t] = type_counts.get(t, 0) + 1

    equipment_type = request.args.get("type", "")
    if equipment_type:
        type_filtered = [i for i in all_items if (i["equipment_type"] or "Uncategorized") == equipment_type]
    else:
        type_filtered = all_items

    # Stat card counts reflect the current type tab but not the status
    # filter itself, so clicking one status card doesn't make the other
    # cards' own counts collapse to 0 -- same idea as the type tab counts.
    counts = {"active": 0, "repair_required": 0, "urgent_repair_required": 0, "out_of_service": 0}
    for item in type_filtered:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    status_filter = request.args.get("status", "")
    if status_filter not in FLEET_STATUSES:
        status_filter = ""
    if status_filter:
        items = [i for i in type_filtered if i["status"] == status_filter]
    else:
        items = type_filtered

    return render_template(
        "dashboard/index.html",
        total_equipment=len(type_filtered),
        total_all=len(all_items),
        counts=counts,
        items=items,
        equipment_type=equipment_type,
        status_filter=status_filter,
        type_counts=sorted(type_counts.items()),
        sort=sort,
        dir=direction,
    )
