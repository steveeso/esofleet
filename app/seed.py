"""Populate the database with a small set of sample equipment — useful for
trying out the app before your real data is loaded. Run with: flask seed-db
"""
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

# A handful of illustrative items spanning the range this app is meant for:
# a hand-held power tool, a pickup truck, and a piece of heavy equipment.
EQUIPMENT = [
    {
        "unit_number": "UNIT-001",
        "equipment_type": "Small Tool",
        "make": "Stihl",
        "model": "MS 271",
        "year": 2022,
        "serial_number": "SN10234567",
        "license_plate": None,
        "fuel_type": "gasoline",
        "meter_type": "hours",
        "meter_reading": 118,
        "status": "active",
        "assigned_to": "North yard",
    },
    {
        "unit_number": "UNIT-002",
        "equipment_type": "Pickup Truck",
        "make": "Ford",
        "model": "F-150",
        "year": 2022,
        "serial_number": "SN20345678",
        "license_plate": "D25558",
        "fuel_type": "gasoline",
        "meter_type": "kilometers",
        "meter_reading": 68432,
        "status": "active",
        "assigned_to": "S. Okafor",
    },
    {
        "unit_number": "UNIT-003",
        "equipment_type": "Heavy Equipment",
        "make": "Caterpillar",
        "model": "320",
        "year": 2019,
        "serial_number": "SN30456789",
        "license_plate": None,
        "fuel_type": "diesel",
        "meter_type": "hours",
        "meter_reading": 3218,
        "status": "in_shop",
        "assigned_to": None,
    },
]

SERVICE_TYPES = [
    "Oil change", "Blade/chain sharpening", "Filter replacement",
    "Annual inspection", "Hydraulic service", "Tire/track service",
]


def run_seed(db):
    admin_hash = generate_password_hash("changeme123")
    db.execute(
        "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        ("admin", admin_hash, "admin"),
    )

    today = date.today()

    for entry in EQUIPMENT:
        cur = db.execute(
            """INSERT OR IGNORE INTO equipment
               (unit_number, equipment_type, make, model, year, serial_number,
                license_plate, fuel_type, meter_type, meter_reading, status, assigned_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry["unit_number"], entry["equipment_type"], entry["make"], entry["model"],
                entry["year"], entry["serial_number"], entry["license_plate"], entry["fuel_type"],
                entry["meter_type"], entry["meter_reading"], entry["status"], entry["assigned_to"],
            ),
        )
        equipment_id = cur.lastrowid
        if not equipment_id:
            continue

        # These are optional/removable fields — mark the ones this sample
        # item actually has a value for as "enabled" so they show up.
        for field in ("year", "serial_number", "license_plate", "fuel_type", "assigned_to"):
            if entry.get(field) is not None:
                db.execute(
                    "INSERT INTO equipment_enabled_fields (equipment_id, field_name) VALUES (?, ?)",
                    (equipment_id, field),
                )

        meter_reading = entry["meter_reading"]
        meter_step = max(1, meter_reading // 10)
        service_date = today - timedelta(days=120)
        service_reading = max(0, meter_reading - meter_step)
        db.execute(
            """INSERT INTO maintenance_records
               (equipment_id, service_date, service_type, description, cost,
                meter_reading_at_service, next_due_date, next_due_meter_reading, performed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                equipment_id, service_date.isoformat(), SERVICE_TYPES[0],
                None, 65.00, service_reading,
                None, None, "Main St Garage",
            ),
        )

        # A small meter-history trail so the chart has something to show.
        earliest_reading = max(0, meter_reading - meter_step * 8)
        for reading, recorded_at in (
            (earliest_reading, (today - timedelta(days=180)).isoformat()),
            (service_reading, service_date.isoformat()),
            (meter_reading, today.isoformat()),
        ):
            db.execute(
                """INSERT INTO equipment_meter_readings (equipment_id, reading, recorded_at)
                   VALUES (?, ?, ?)""",
                (equipment_id, reading, recorded_at),
            )

    db.commit()
