# Fleet Ops — Vehicle & Maintenance Tracker

A small Flask web app for tracking a vehicle fleet: vehicle records (make,
model, VIN, plate, mileage, status, driver) and maintenance history (service
records, next due date/mileage), with a dashboard that flags vehicles that
are overdue or due soon for service. Supports multiple logins.

This is a proof-of-concept meant to run locally first. It uses SQLite, so
there's no separate database server to set up.

## 1. Requirements

- Python 3.9+ (tested on 3.12)
- pip

## 2. Setup

From the `fleet-app` folder:

**Windows (PowerShell):**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Initialize the database

This creates the SQLite database and tables. **Running this wipes any
existing data**, so only do it once at the start (or if you want a clean
reset).

```bash
# Windows
set FLASK_APP=run.py
python -m flask init-db

# Linux/macOS
export FLASK_APP=run.py
python -m flask init-db
```

### Optional: load sample data to try it out
```bash
python -m flask seed-db
```
This creates ~40 sample vehicles and an admin login:
- **Username:** `admin`
- **Password:** `changeme123`

If you'd rather start empty and add your first admin user yourself, skip
`seed-db` and see step 5 below.

## 4. Run it

```bash
python run.py
```

Then open **http://127.0.0.1:5000** in your browser. If you used `seed-db`,
log in with `admin` / `changeme123` — **change this password immediately**
(there's no self-service password change yet; ask whoever's helping you
extend the app to add one, or update it directly in the database).

## 5. Creating your first user without sample data

If you skipped `seed-db`, there's no login yet. The quickest way to create
one is a short Python snippet:

```bash
python -c "
from app import create_app
from app.db import get_db
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    db = get_db()
    db.execute(
        'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
        ('admin', generate_password_hash('CHANGE-THIS-PASSWORD'), 'admin')
    )
    db.commit()
    print('Created admin user.')
"
```

Once you're logged in as an admin, you can add further users from the
**Users** page in the sidebar — no command line needed after that.

## 6. What's included

- **Dashboard** — counts of active/due-soon/overdue vehicles, and a list of
  vehicles needing attention (soonest due first).
- **Vehicles** — searchable, filterable list; add/edit/remove; VIN and unit
  number are checked for duplicates.
- **Vehicle detail** — full record plus maintenance history.
- **Maintenance records** — log service with date, type, cost, mileage,
  next due date/mileage, and who performed it. Logging a record updates the
  vehicle's odometer automatically if the new mileage is higher.
- **Users (admin only)** — add/remove logins, set role (admin/editor).

"Due soon" is within 14 days or 500 miles of the next-due threshold from a
vehicle's most recent service record; you can adjust these thresholds in
`app/routers/dashboard.py` (`DUE_SOON_DAYS`, `DUE_SOON_MILEAGE`).

## 7. Notes on this being a proof of concept

- **Secret key:** the app uses a default `SECRET_KEY` for local testing.
  Before running this anywhere besides your own machine, set the
  `FLEET_SECRET_KEY` environment variable to a random value.
- **Dev server:** `python run.py` runs Flask's built-in development server,
  which is fine for local testing but isn't meant for production hosting.
  When you're ready to move this to your Linux server, it should run behind
  a proper WSGI server (e.g. gunicorn) instead.
- **Database file** lives at `instance/fleet.sqlite`. Back it up before any
  risky changes — there's no undo for deletions yet.
- **No password reset flow, no CSV import, no email/SMS alerts yet** — these
  are natural next steps once the core shape feels right.

## 8. Project structure

```
fleet-app/
  app/
    __init__.py       # app factory
    db.py              # database connection + CLI commands
    schema.sql         # table definitions
    seed.py            # sample data generator
    routers/           # route handlers (auth, dashboard, vehicles, maintenance, users)
    templates/         # Jinja2 HTML templates
    static/css/        # stylesheet
  run.py               # entry point
  requirements.txt
```
