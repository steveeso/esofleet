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
- **Parts location grew from a free-text field into a physical wayfinding
  feature.** It started as a plain `location` string (e.g. "Shop shelf
  A3"). It's now optionally a real warehouse position (Aisle/Bank/Shelf)
  that renders an actual highlighted floor plan on the part's detail page
  — because "where is this thing" kept meaning "physically walk to the
  correct shelf," not just "read a text label." The plain-text option is
  kept and still the default for anything outside the warehouse (another
  building, a vendor) — this was never meant to force every location into
  the warehouse model.

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
- **`location` vs `location_code`**: `catalog_items.location` is always the
  human-readable string shown everywhere (e.g. "Aisle 07, Bank B, Shelf
  4" or free text like "Your dad's house"); `location_code` is a second,
  optional column that's only ever non-NULL when `location` was set via
  the warehouse picker, and holds the compact, uniform, machine-sortable
  form ("gm_down-07-B-4") that `app/warehouse.py`'s `parse_code()` turns
  back into a room/aisle/bank/shelf tuple for rendering the floor plan.
  Editing `location` as plain text (the Inventory Audit page's inline
  editor is the only place this can happen) clears `location_code` rather
  than trying to guess whether the new text still matches — a stale code
  would silently highlight the wrong shelf, which is worse than showing no
  floor plan at all.

## Warehouse layout and floor plan rendering

- **`app/warehouse.py` is the single source of truth** for which
  Aisle/Bank/Shelf combinations exist, in every room. It's keyed by room
  id (`ROOMS = {"gm_down": {...}, ...}`) specifically so a second room —
  another building, another floor — is just a new entry with its own
  aisle layout and its own building geometry (wall outline, doors, shelf
  rectangles), not a rewrite of the picker, the composer/parser, or the
  renderer. Only `gm_down` exists today.
  - Room ids can't contain a hyphen — `location_code` is
    `room-aisle-bank-shelf`, hyphen-delimited, so a hyphen inside the room
    id itself would make the format ambiguous to parse back apart. Use an
    underscore instead (`gm_down`, not `gm-down`) and put the human-facing
    name in that room's `label` (`"GM-Down"`) instead.
  - A structure's `banks` count and `dir` (N-S vs E-W) are the only
    things the Aisle/Bank picker and the floor-plan renderer read — so
    they can never quietly drift out of sync with each other the way two
    separately-maintained lists could.
  - Wall/door geometry for a room is real, hand-measured data (traced from
    a CubiCasa scan of the actual building plus the real shelving unit
    dimensions — 25in deep × 6ft long × 6ft tall, 5 shelves), not
    something the code derives or guesses. Adding a second room means
    supplying that same kind of real measurement, not just picking numbers
    that look reasonable.
- **`app/floorplan.py` renders `warehouse.py`'s data as plain inline SVG**
  — no image libraries, no filesystem or network access, nothing to
  cache. Generating one costs microseconds, same order of magnitude as
  rendering any other template fragment, so it's just called fresh on
  every page load rather than optimized.
  - Design intent: exactly one bank/shelf is ever the reason someone
    opened the page. Everything else is deliberately pushed down in size,
    weight, and opacity (faint gray, small type) so it reads as
    background structure rather than competing information, and the
    target gets a bold, high-contrast "chip" callout instead of blending
    into a grid of equally-loud labels. If this ever needs a "no
    highlight, show everything at full weight" overview mode, that
    hierarchy will need revisiting — it currently assumes there's always
    exactly one target.
  - A structure can override where its aisle-number label is drawn
    (`label_pos`) when the default (north of a north-south run, west of
    an east-west one) would collide with a real neighboring structure —
    see Aisle 08's entry in `warehouse.py`, which sits almost flush under
    Aisle 09. This is deliberately a per-structure data override, not a
    special case in the renderer, so it stays obvious *why* that one
    aisle is different when someone's staring at the room, not the code.
  - Every render takes a `uid` used to namespace its internal `<defs>`
    element ids (e.g. the highlight drop-shadow filter). This exists
    because the item detail page embeds the *same* rendered diagram
    twice — a small thumbnail and a larger copy inside the expand-on-click
    dialog — and two SVGs with identically-`id`'d `<defs>` in the same
    HTML document collide. Any future place that embeds more than one
    instance of a rendered diagram on one page needs to pass distinct
    `uid`s too.

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
- The warehouse location picker (item form) only ever edits the default
  room — there's no room selector in the UI yet, since only one room
  (`gm_down`) has a layout defined. A location in some other room would
  still save/display fine (via `location`/`location_code`), it just
  wouldn't be reachable from the picker's dropdowns; it'd open in "Custom"
  mode showing the plain text instead.
- The Inventory Audit page's inline Location editor is plain text only —
  no Aisle/Bank/Shelf dropdowns there, by design (a compact table row
  isn't a great place for a 3-dropdown picker). Reassigning a warehouse
  shelf position goes through the full Edit item form.
