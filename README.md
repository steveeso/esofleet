# EsoFleet — Equipment & Maintenance Tracker

A small Flask web app for tracking a fleet of equipment — anything from
power tools and mowers to trucks and heavy machinery: equipment records
(equipment type, make, model, serial number, plate, fuel type, meter reading
in kilometers, hours, or miles, status, assignee) linked to a shared Parts
Catalog — reference specs like filters and oil types/capacities, each with
alternate part numbers — so one catalog entry (e.g. "Oil Filter: PN-12345")
can be reused, and updated, across every equipment item that uses it.
Supports multiple logins.

Required/scheduled maintenance (due dates, overdue/due-soon tracking) is on
hold for now; the current focus is this persistent reference info. The
underlying maintenance-records log and due-date fields still exist in the
code and database for when that work resumes.

This is a proof-of-concept meant to run locally first. It uses SQLite, so
there's no separate database server to set up.

## 1. Requirements

- Python 3.9+ (tested on 3.12)
- pip

## 2. Setup

From the `EsoFleet` folder:

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

This creates the SQLite database and any tables/indexes that don't exist
yet. It's safe to re-run any time, including against a database you've
already been using — it never drops a table or touches existing rows, so
pulling in a newer version of the app won't wipe your data.

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
This creates 3 sample items — a power tool, a pickup truck, and a piece of
heavy equipment — and an admin login:
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

- **Dashboard** — equipment counts by status, plus a sortable table of all
  equipment (click any column header to sort by it, click again to reverse).
- **Equipment** — searchable, filterable list; add/edit/remove; unit number
  and serial number are checked for duplicates. Only **unit number, equipment
  type, make, model, status, and meter** are required on every item; the
  rest is opt-in per unit, added and removed from the Edit Equipment screen
  the way custom fields work — a field only shows up on the form and detail
  page once you've added it:
  - an **equipment type** (Automotive, Pickup Truck, Heavy Truck, Trailer,
    Heavy Equipment, Light Equipment, Generator, Boat, Small Tool — this
    list is just a starting point and easy to extend in
    `app/routers/equipment.py` since it isn't enforced by the database) —
    required;
  - a **meter type** (kilometers, hours, or miles — kilometers is the
    default) so road vehicles track distance, power tools and heavy
    equipment can track hours, and the occasional item can still use miles —
    required (leave the reading at 0 for anything that isn't really metered);
  - optional built-in fields: **Year, Serial number, License plate, Fuel
    type, Assigned to, Notes** — each is its own on/off toggle per unit
    (an "Additional fields" panel on the Edit Equipment screen lets you add
    one, or remove one — which also clears its value);
  - any number of **custom fields** (label + value, e.g. "Gross Vehicle
    Weight" or "Snowplow Capable") for anything the built-in fields don't
    cover — add, edit, or remove them from the same screen; they show up
    alongside the built-in fields on the item's detail page.
- **Equipment detail** — a single page with a small left-hand nav to jump
  between its three sections:
  - **Equipment Info** — the fields above, plus a meter history chart and
    log (every recorded reading is timestamped; the date defaults to today
    but is editable so a reading can be backdated — the equipment's current
    meter value always reflects whichever logged reading is chronologically
    most recent, regardless of entry order);
  - **Maintenance Info** — catalog items linked to this equipment item,
    grouped by category, shown as collapsible sections you expand in
    place: each item shows its primary value (e.g. "Oil Filter: PN-12345")
    and, expanded further, an optional **quantity + unit of measure on
    this unit** (decimals allowed — "2" filters, "5.7 liters" of oil,
    "1.25 lb" of refrigerant) plus any alternate part numbers/specs.
    Quantity lives on the link, not the catalog item, so the same "Oil
    Filter" can be ×1 on one truck and ×2 on another. "+ Link item" lets
    you attach an existing catalog item or create a new one on the spot
    (with its own optional quantity/unit); "Unlink" removes it from just
    this equipment item, leaving the catalog entry (and its links to
    other equipment) intact. Right below the catalog links, in the same
    list, you can also add label/value/quantity/unit specs that genuinely
    aren't meant to be shared (e.g. this particular truck's oil capacity) —
    added, edited, and removed the same way custom fields are, but
    scoped to just this one item;
  - **Maintenance History** — the service log below.
- **Parts Catalog** (its own page, in the sidebar) — the shared catalog
  behind Maintenance Info: categories, items, and alternates, independent
  of any one equipment item, so it can be browsed or extended on its own
  and reused across the fleet. Editing an item or its alternates here (or
  from any equipment item's Maintenance Info) applies everywhere it's
  linked.
- **Maintenance records** — a service history log (date, type, cost, meter
  reading, next due date/meter reading, performed by). Logging a record
  also feeds its meter reading into the equipment's meter history. The
  next-due fields are captured but not currently used to flag anything —
  see the note above about scheduled maintenance being on hold.
- **Users (admin only)** — add/remove logins, set role (admin/editor).

## 7. Notes on this being a proof of concept

- **Secret key:** the app uses a default `SECRET_KEY` for local testing.
  Before running this anywhere besides your own machine — including
  sharing it with trusted users over Tailscale — set the
  `FLEET_SECRET_KEY` environment variable to a random value.
- **Debug mode is off by default.** The Werkzeug interactive debugger it
  enables lets anyone who can reach an unhandled exception run arbitrary
  Python, which is fine on localhost but not once anyone else can reach
  the app. Opt in for local development only with `FLASK_DEBUG=1 python
  run.py` (PowerShell: `$env:FLASK_DEBUG=1; python run.py`).
- **Dev server:** `python run.py` runs Flask's built-in development server,
  which is fine for local testing or a handful of trusted users (e.g. over
  Tailscale) but isn't hardened for the open internet. If you ever want it
  properly production-grade, it should run behind a real WSGI server
  (e.g. gunicorn) instead.
- **Database file** lives at `instance/fleet.sqlite`. Back it up before any
  risky changes — there's no undo for deletions yet.
- **Schema changes are additive.** `schema.sql` only ever creates tables
  (`CREATE TABLE IF NOT EXISTS`); anything that can't be expressed that way
  (e.g. replacing an old table with a new one) goes through a small,
  one-off migration step in `db.py`'s `_migrate()`, written so it's safe to
  run repeatedly and never discards data that might be real. If a future
  change needs to add a column to an existing table, that'll need its own
  `ALTER TABLE` step there too — `CREATE TABLE IF NOT EXISTS` alone won't
  add columns to a table that already exists.
- **No password reset flow, no CSV import, no email/SMS alerts yet** — these
  are natural next steps once the core shape feels right.

## 8. Project structure

```
EsoFleet/
  app/
    __init__.py       # app factory
    db.py              # database connection + CLI commands
    schema.sql         # table definitions
    seed.py            # sample data generator
    routers/           # route handlers (auth, catalog, dashboard, equipment, maintenance, users)
    templates/         # Jinja2 HTML templates
    static/css/        # stylesheet
  run.py               # entry point
  requirements.txt
```
