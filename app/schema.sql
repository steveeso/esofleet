-- Every statement here is additive (CREATE ... IF NOT EXISTS) so this file
-- is safe to re-run against an existing database — it never drops a table
-- or touches existing rows. Adding a column to an already-existing table
-- needs an explicit migration step instead (see db.py's _migrate()).

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_number TEXT UNIQUE NOT NULL,
    equipment_type TEXT NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER,
    serial_number TEXT UNIQUE,
    license_plate TEXT,
    plate_number TEXT,
    fuel_type TEXT CHECK (fuel_type IN ('gasoline', 'diesel', 'propane', 'electric', 'other')),
    meter_type TEXT NOT NULL DEFAULT 'kilometers' CHECK (meter_type IN ('kilometers', 'hours', 'miles')),
    meter_reading INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    assigned_to TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which of the optional built-in fields (year, serial_number,
-- license_plate, fuel_type, assigned_to, notes — see equipment.py's
-- OPTIONAL_FIELDS) are turned on for a given equipment item. The
-- underlying equipment column is only ever populated while its field_name
-- is present here; removing a field clears the column and deletes this row.
CREATE TABLE IF NOT EXISTS equipment_enabled_fields (
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    PRIMARY KEY (equipment_id, field_name)
);

-- Ad-hoc extra fields for equipment that don't fit the fixed columns above
-- (e.g. "Gross Vehicle Weight", "Snowplow Capable") — added/removed per
-- item from the Edit Equipment screen.
CREATE TABLE IF NOT EXISTS equipment_custom_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Timestamped meter-reading history — every reading that gets recorded
-- (equipment creation/edit, a logged maintenance record, or a dedicated
-- reading log entry) lands here so it can be charted over time. recorded_at
-- is user-editable so a reading can be backdated; equipment.meter_reading
-- always mirrors whichever row here is chronologically most recent.
CREATE TABLE IF NOT EXISTS equipment_meter_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    reading INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    service_date TEXT NOT NULL,
    service_type TEXT NOT NULL,
    description TEXT,
    cost REAL,
    meter_reading_at_service INTEGER,
    next_due_date TEXT,
    next_due_meter_reading INTEGER,
    performed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Shared reference catalog (filters, oil types and capacities, etc.) — NOT
-- owned by any single equipment item. Categories group items (e.g. "Oil
-- Filter: PN-XYZ"), and items can list alternate part numbers/specs.
-- Equipment items link to catalog items (see equipment_catalog_items)
-- rather than owning private copies, so one catalog entry can be reused —
-- and updated — across every equipment item that uses it.
CREATE TABLE IF NOT EXISTS catalog_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS catalog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value TEXT,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS catalog_item_alternates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    value TEXT NOT NULL,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equipment_catalog_items (
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    catalog_item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    quantity REAL,
    quantity_unit TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (equipment_id, catalog_item_id)
);

-- Unlike catalog_items, these belong to one equipment item only — for specs
-- that genuinely aren't meant to be shared (e.g. this specific unit's oil
-- capacity). Shown in the Maintenance Info section alongside catalog links,
-- but never linkable from other equipment.
CREATE TABLE IF NOT EXISTS equipment_info_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value TEXT,
    quantity REAL,
    quantity_unit TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_maintenance_equipment ON maintenance_records(equipment_id);
CREATE INDEX IF NOT EXISTS idx_equipment_status ON equipment(status);
CREATE INDEX IF NOT EXISTS idx_custom_fields_equipment ON equipment_custom_fields(equipment_id);
CREATE INDEX IF NOT EXISTS idx_enabled_fields_equipment ON equipment_enabled_fields(equipment_id);
CREATE INDEX IF NOT EXISTS idx_meter_readings_equipment ON equipment_meter_readings(equipment_id);
CREATE INDEX IF NOT EXISTS idx_catalog_items_category ON catalog_items(category_id);
CREATE INDEX IF NOT EXISTS idx_catalog_alternates_item ON catalog_item_alternates(catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_equipment_catalog_items_item ON equipment_catalog_items(catalog_item_id);
CREATE INDEX IF NOT EXISTS idx_info_items_equipment ON equipment_info_items(equipment_id);
