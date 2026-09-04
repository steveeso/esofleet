"""Populate the database with sample fleet data — useful for trying out
the app before your real data is loaded. Run with: flask seed-db
"""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

MAKES_MODELS = [
    ("Ford", "F-150"), ("Ford", "Transit"), ("Chevrolet", "Silverado 2500"),
    ("Ram", "1500"), ("Freightliner", "Cascadia"), ("International", "MV"),
    ("Toyota", "Tacoma"), ("GMC", "Savana"), ("Isuzu", "NPR"), ("Ford", "Explorer"),
]
STATUSES = ["active", "active", "active", "active", "in_shop"]
SERVICE_TYPES = ["Oil change", "Brake inspection", "Tire rotation", "Annual DOT inspection", "Transmission service"]
DRIVERS = ["J. Alvarez", "M. Chen", "S. Okafor", "T. Bergstrom", "R. Singh", None, None]


def run_seed(db):
    admin_hash = generate_password_hash("changeme123")
    db.execute(
        "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        ("admin", admin_hash, "admin"),
    )

    today = date.today()

    for i in range(1, 41):
        make, model = random.choice(MAKES_MODELS)
        unit_number = f"UNIT-{i:03d}"
        vin = f"1FT{random.randint(10**11, 10**12 - 1)}"
        mileage = random.randint(8000, 145000)
        status = random.choice(STATUSES)
        cur = db.execute(
            """INSERT OR IGNORE INTO vehicles
               (unit_number, make, model, year, vin, license_plate, mileage, status, assigned_driver)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit_number, make, model, random.randint(2016, 2024), vin,
                f"{random.choice('ABCDEFG')}{random.randint(10000,99999)}",
                mileage, status, random.choice(DRIVERS),
            ),
        )
        vehicle_id = cur.lastrowid
        if not vehicle_id:
            continue

        # 1-3 historical maintenance records per vehicle
        for _ in range(random.randint(1, 3)):
            days_ago = random.randint(10, 400)
            service_date = today - timedelta(days=days_ago)
            due_offset = random.choice([-10, -3, 5, 20, 60, 120])
            db.execute(
                """INSERT INTO maintenance_records
                   (vehicle_id, service_date, service_type, description, cost,
                    mileage_at_service, next_due_date, next_due_mileage, performed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vehicle_id, service_date.isoformat(), random.choice(SERVICE_TYPES),
                    None, round(random.uniform(45, 850), 2),
                    max(0, mileage - random.randint(500, 5000)),
                    (today + timedelta(days=due_offset)).isoformat(),
                    mileage + random.randint(-300, 3000) if random.random() > 0.4 else None,
                    random.choice(["Main St Garage", "Fleet Depot", None]),
                ),
            )

    db.commit()
