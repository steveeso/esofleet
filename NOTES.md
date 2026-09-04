# Project notes

Not a changelog — the *why* behind decisions that aren't obvious just from
reading the code, kept here so a fresh session (different machine, different
assistant, or just future-you) can get oriented quickly. Update this when a
decision is made that a future reader would otherwise have to guess at.

## Scope and direction

- This started as a vehicle fleet tracker and was deliberately generalized
  to **Equipment** — anything from hand tools to heavy machinery — because
  the fleet isn't just road vehicles. Fields that only make sense for
  vehicles (VIN, mileage) were renamed/generalized accordingly (serial
  number, meter reading).
- **Required/scheduled maintenance (due dates, overdue tracking) is
  intentionally on hold.** The current focus is *persistent* maintenance
  reference info instead. The underlying `maintenance_records` table,
  `next_due_date`/`next_due_meter_reading` columns, and the due-soon
  calculation logic in `app/routers/dashboard.py` (`_status_for`,
  `DUE_SOON_DAYS`, `DUE_SOON_METER`) all still exist and work — they're
  just not called from the dashboard route anymore. Wiring them back in
  later should be straightforward; the logic wasn't deleted, just unhooked.
- **Only Unit number, Equipment type, Make, Model, Status, and Meter are
  required** on an equipment item. Everything else (Year, Serial number,
  License plate, Fuel type, Assigned to, Notes) is opt-in per unit, added
  and removed the same way custom fields are — because a chainsaw doesn't
  have a license plate and a trailer doesn't have a fuel type, and forcing
  every field onto every item made the edit screen useless noise.
  - Meter stayed mandatory (not opt-in) specifically because it drives the
    Meter History chart/table — making it optional would mean handling a
    "no meter" state throughout the app. If an item genuinely isn't
    metered, the convention is to just leave the reading at 0.
  - Equipment type is genuinely required (no "Unspecified" option) — the
    dashboard and equipment list both sort/group by it.

## Data model choices worth knowing

- **Parts Catalog vs. per-equipment info**: `catalog_items` (Filters, Oil
  Types, etc.) are shared and meant to be reused — linking an "Oil Filter"
  catalog item to two different trucks means editing it once updates it
  everywhere it's linked. `equipment_info_items` exist for the opposite
  case: specs that are genuinely specific to one unit (e.g. this
  particular truck's oil capacity) and shouldn't live in the shared
  catalog at all. Both render in the same Maintenance Info panel with the
  same look — that's deliberate, not an oversight.
- **Quantity lives on the link, not the item.** `equipment_catalog_items`
  (the join table between equipment and catalog items) has its own
  `quantity`/`quantity_unit` columns, separate from the catalog item
  itself — because the same "Oil Filter" might be qty 1 on one truck and
  qty 2 on another. Quantity is `REAL` (not `INTEGER`) specifically to
  support fractional amounts like "5.7 liters" or "1.25 lb" of
  refrigerant, not just parts counts.
- **Custom fields vs. optional built-in fields**: both live in the same
  "Additional fields" UI on the Edit Equipment screen and look identical,
  but they're different mechanisms. Built-in optional fields
  (`equipment_enabled_fields` + the real `equipment.year`/`serial_number`/
  etc. columns) are for the fixed, known set with proper typing/validation
  (e.g. fuel type is a dropdown, serial number is unique). Custom fields
  (`equipment_custom_fields`) are fully free-form label/value pairs for
  anything not anticipated. The "+ Add field" picker on that screen offers
  both through one control (pick a known field, or type a new name), but
  they're stored and validated differently under the hood.

## Migration policy

- `schema.sql` is additive-only: every statement is
  `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. It is never
  safe to add a bare `ALTER TABLE` or `DROP TABLE` there.
- Anything that can't be expressed that way (new column on an existing
  table, one-time data backfill, replacing an old table) goes through
  `db.py`'s `_migrate()`, tracked in a `schema_migrations` table so each
  step runs exactly once. This exists because early in this project's
  life, repeated schema changes via a destructive `init-db` silently wiped
  real test data more than once — `init-db` is now safe to re-run against
  a database with real data in it, and that guarantee should not be
  broken by a future change that reaches for `DROP TABLE` as a shortcut.

## Deployment / access

- Runs locally via `python run.py` (Flask dev server). `FLASK_DEBUG=1` is
  required to opt into debug mode / the auto-reloader — it defaults off
  because the app is sometimes exposed beyond localhost (see below), and
  the Werkzeug interactive debugger is a remote-code-execution risk if
  reachable by anyone but the developer.
- `FLEET_SECRET_KEY` must be set to a real random value before the app is
  reachable by anyone else — the code ships with an obvious placeholder
  default, and Flask session cookies are only as trustworthy as this key.
- Currently made reachable to a small set of trusted users via **Tailscale
  Funnel** (public HTTPS URL, proxied to local port 5000) rather than
  opening any port directly. This machine is meant to be the single
  running instance — SQLite is a local file, so a second independent copy
  running elsewhere would silently diverge into a different dataset, not
  stay in sync. Other workstations should either use the running instance
  directly (browser, or RDP into this machine over Tailscale) or treat
  themselves as pure code-editing clients (`git pull`/`git push`) rather
  than running their own live copy with its own database.

## Known gaps (not yet built, on purpose)

- No password change or reset flow for any user — the only way to fix a
  bad password today is editing the database directly. Wanted, not yet
  built (see git history / ask for context if it's still missing).
- Password *recovery* (forgot-password via email) is deliberately out of
  scope for now — would require setting up outbound email, which hasn't
  been wanted yet. Don't build this without it being explicitly asked for.
- No CSV import, no email/SMS alerts.
