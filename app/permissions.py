"""The fixed set of things a user can be allowed to do, plus helpers to work
out who is allowed what.

A user's effective permissions are: their role's permissions, then any
per-user override on top (an explicit allow or deny wins over the role).
The built-in "admin" role always has every permission and can't be edited,
and managing users/roles is admin-only rather than a grantable permission --
so nobody can be configured into (or out of) being locked out of this page.
"""

ADMIN_ROLE = "admin"

# (key, label, group, description). Order here is display order.
PERMISSIONS = [
    ("equipment.edit", "Add & edit equipment", "Equipment",
     "Create units, edit their details and custom fields, log meter readings, decode VINs, and link or edit maintenance info."),
    ("equipment.delete", "Delete equipment", "Equipment",
     "Delete a unit entirely, or delete a meter reading."),
    ("equipment.import", "Import equipment from CSV", "Equipment",
     "Bulk-create or update units from a CSV file."),
    ("maintenance.edit", "Log & edit maintenance", "Maintenance",
     "Add and edit maintenance records on a unit."),
    ("maintenance.delete", "Delete maintenance records", "Maintenance", ""),
    ("catalog.edit", "Add & edit parts catalog", "Parts catalog",
     "Create and edit categories, parts, alternates and specs."),
    ("catalog.delete", "Delete parts & categories", "Parts catalog", ""),
    ("catalog.import", "Import parts from CSV", "Parts catalog", ""),
    ("inventory.edit", "Edit stock counts & locations", "Inventory",
     "Change a part's quantity on hand and shelf location (Inventory Audit page)."),
    ("suppliers.edit", "Add & edit suppliers", "Suppliers",
     "Add, edit and deactivate suppliers and their contacts, and edit the supplier category list."),
]

ALL_KEYS = [p[0] for p in PERMISSIONS]
PERMISSION_LABELS = {p[0]: p[1] for p in PERMISSIONS}

# Built-in roles seeded on first migration. "editor" keeps every permission
# so existing editors behave exactly as before this system existed.
BUILTIN_ROLES = {
    "admin": ("Full access, including managing users and roles.", list(ALL_KEYS)),
    "editor": ("Can add and change everything.", list(ALL_KEYS)),
    "viewer": ("Read-only.", []),
}


def groups():
    """PERMISSIONS as [(group_name, [(key, label, description), ...]), ...]."""
    out = {}
    for key, label, group, desc in PERMISSIONS:
        out.setdefault(group, []).append((key, label, desc))
    return list(out.items())


def role_names(db):
    return [r["name"] for r in db.execute("SELECT name FROM roles ORDER BY is_builtin DESC, name")]


def role_permissions(db, role):
    if role == ADMIN_ROLE:
        return set(ALL_KEYS)
    rows = db.execute("SELECT permission FROM role_permissions WHERE role = ?", (role,)).fetchall()
    return {r["permission"] for r in rows} & set(ALL_KEYS)


def user_overrides(db, user_id):
    """{permission: True (explicit allow) | False (explicit deny)}."""
    rows = db.execute(
        "SELECT permission, allowed FROM user_permissions WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r["permission"]: bool(r["allowed"]) for r in rows if r["permission"] in ALL_KEYS}


def effective_permissions(db, user_id, role):
    if role == ADMIN_ROLE:
        return set(ALL_KEYS)
    perms = role_permissions(db, role)
    for key, allowed in user_overrides(db, user_id).items():
        if allowed:
            perms.add(key)
        else:
            perms.discard(key)
    return perms
