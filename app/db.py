import sqlite3
from datetime import datetime, date

import click
from flask import current_app, g

from . import permissions


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create any tables/indexes that don't exist yet. Safe to run against
    an existing database — schema.sql is additive (CREATE ... IF NOT
    EXISTS), so this never drops a table or touches existing rows."""
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))
    _migrate(db)


def _migrate(db):
    """One-off migration steps schema.sql's CREATE ... IF NOT EXISTS can't
    express on its own (e.g. replacing a table, or backfilling a new
    tracking table from existing data). Each step must be safe to run
    repeatedly and must never discard data that might be real."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )

    # The old per-equipment maintenance-info tables were replaced by the
    # shared catalog_* tables. Drop them only if empty, so this can never
    # discard data — if they're not empty (an old install), they're just
    # left in place, unused, rather than risk deleting something real.
    for legacy_table in (
        "maintenance_info_alternates", "maintenance_info_items", "maintenance_info_categories",
    ):
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (legacy_table,)
        ).fetchone()
        if exists:
            count = db.execute(f"SELECT COUNT(*) AS n FROM {legacy_table}").fetchone()["n"]
            if count == 0:
                db.execute(f"DROP TABLE {legacy_table}")

    _backfill_enabled_fields(db)
    _remap_equipment_statuses(db)
    _seed_roles(db)
    _seed_supplier_categories(db)
    _grant_new_permission(db, "0005_grant_suppliers_edit", "suppliers.edit")
    _ensure_column(db, "equipment_catalog_items", "quantity", "REAL")
    _ensure_column(db, "equipment_info_items", "quantity", "REAL")
    _ensure_column(db, "equipment_catalog_items", "quantity_unit", "TEXT")
    _ensure_column(db, "equipment_info_items", "quantity_unit", "TEXT")
    _ensure_column(db, "equipment", "plate_number", "TEXT")
    _ensure_column(db, "catalog_items", "location", "TEXT")
    _ensure_column(db, "catalog_items", "location_code", "TEXT")
    _ensure_column(db, "catalog_items", "quantity_on_hand", "INTEGER")
    _ensure_column(db, "catalog_item_specs", "flagged", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(db, "catalog_items", "supplier_stock", "INTEGER")
    _ensure_column(db, "catalog_items", "supplier_stock_checked_at", "TEXT")
    _ensure_column(db, "catalog_items", "napa_stock", "INTEGER")
    _ensure_column(db, "catalog_items", "napa_stock_checked_at", "TEXT")
    _ensure_column(db, "equipment", "engine_size", "TEXT")
    _ensure_column(db, "equipment", "engine_configuration", "TEXT")
    _ensure_column(db, "equipment", "factory_gvw", "TEXT")

    db.commit()


def _migration_applied(db, name):
    return db.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
    ).fetchone() is not None


def _mark_migration_applied(db, name):
    db.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (name,))


def _ensure_column(db, table, column, decl):
    """Add a column to an existing table if it's not already there. Safe to
    run repeatedly (checked via PRAGMA table_info first) and additive only —
    for when CREATE TABLE IF NOT EXISTS alone can't add a column to a table
    that already exists on an older database."""
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _backfill_enabled_fields(db):
    """One-time only (tracked in schema_migrations): mark the optional
    built-in fields (year, serial_number, license_plate, fuel_type,
    assigned_to, notes) as 'enabled' for any equipment item that already
    has a value in that column, so existing data doesn't just vanish from
    the edit screen now that these fields are opt-in. Only runs once —
    otherwise a field a user later removes (which clears the column) would
    get silently re-enabled on the next init-db if it happened to be
    non-null for some other reason."""
    name = "0001_backfill_enabled_fields"
    if _migration_applied(db, name):
        return
    for field in ("year", "serial_number", "license_plate", "fuel_type", "assigned_to", "notes"):
        db.execute(
            f"""INSERT OR IGNORE INTO equipment_enabled_fields (equipment_id, field_name)
                SELECT id, ? FROM equipment WHERE {field} IS NOT NULL""",
            (field,),
        )
    _mark_migration_applied(db, name)


def _remap_equipment_statuses(db):
    """One-time only (tracked in schema_migrations): the equipment.status
    vocabulary changed from active/in_shop/retired to active/repair_required/
    urgent_repair_required/out_of_service. Remap existing rows so old values
    don't linger as statuses the UI no longer has colors or labels for."""
    name = "0002_remap_equipment_statuses"
    if _migration_applied(db, name):
        return
    db.execute("UPDATE equipment SET status = 'repair_required' WHERE status = 'in_shop'")
    db.execute("UPDATE equipment SET status = 'out_of_service' WHERE status = 'retired'")
    _mark_migration_applied(db, name)


def _seed_roles(db):
    """One-time only: create the built-in roles and their default
    permissions. Existing users keep their admin/editor role; "editor" gets
    every permission so nothing changes for them until an admin edits it."""
    name = "0003_seed_roles"
    if _migration_applied(db, name):
        return
    for role, (description, perms) in permissions.BUILTIN_ROLES.items():
        db.execute(
            "INSERT OR IGNORE INTO roles (name, description, is_builtin) VALUES (?, ?, 1)",
            (role, description),
        )
        for perm in perms:
            db.execute(
                "INSERT OR IGNORE INTO role_permissions (role, permission) VALUES (?, ?)",
                (role, perm),
            )
    _mark_migration_applied(db, name)


SUPPLIER_CATEGORIES = [
    "Parts", "Filters", "Tires", "Dealer", "Glass", "Hydraulics",
    "Towing", "Fuel", "Fabrication", "Other",
]


def _seed_supplier_categories(db):
    """One-time only: the starting supplier category list. Tracked so a
    category an admin later deletes or renames doesn't come back on the
    next startup."""
    name = "0004_seed_supplier_categories"
    if _migration_applied(db, name):
        return
    for order, category in enumerate(SUPPLIER_CATEGORIES):
        db.execute(
            "INSERT OR IGNORE INTO supplier_categories (name, sort_order) VALUES (?, ?)",
            (category, order),
        )
    _mark_migration_applied(db, name)


def _grant_new_permission(db, name, permission):
    """One-time only: give a newly added permission to the built-in editor
    role, matching 0003_seed_roles' promise that editors can change
    everything. Custom roles are left alone -- an admin decides for those --
    and an admin who later unticks it for editors keeps that choice."""
    if _migration_applied(db, name):
        return
    if db.execute("SELECT 1 FROM roles WHERE name = 'editor'").fetchone():
        db.execute(
            "INSERT OR IGNORE INTO role_permissions (role, permission) VALUES ('editor', ?)",
            (permission,),
        )
    _mark_migration_applied(db, name)


@click.command("init-db")
def init_db_command():
    """Create any missing database tables/indexes. Safe to re-run — never
    wipes existing data."""
    init_db()
    click.echo("Database is up to date.")


@click.command("seed-db")
def seed_db_command():
    """Populate the database with sample fleet data for testing."""
    from .seed import run_seed

    run_seed(get_db())
    click.echo("Seeded the database with sample data.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)
