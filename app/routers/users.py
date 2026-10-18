import re
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .. import permissions
from ..db import get_db
from .auth import admin_required

bp = Blueprint("users", __name__, url_prefix="/users")

ROLE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,29}$")


def _user_or_404(db, user_id):
    user = db.execute(
        "SELECT id, username, role, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None:
        abort(404)
    return user


def _admin_count(db):
    return db.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = ?", (permissions.ADMIN_ROLE,)
    ).fetchone()["n"]


def _would_strand_admins(db, user):
    """True if `user` is the only admin, so demoting or removing them would
    leave nobody able to manage users."""
    return user["role"] == permissions.ADMIN_ROLE and _admin_count(db) <= 1


@bp.route("/")
@admin_required
def list_users():
    db = get_db()
    users = db.execute(
        """SELECT users.id, users.username, users.role, users.created_at,
                  (SELECT COUNT(*) FROM user_permissions WHERE user_id = users.id) AS override_count
           FROM users ORDER BY users.username"""
    ).fetchall()
    return render_template("users/list.html", users=users, roles=permissions.role_names(db))


@bp.route("/new", methods=("POST",))
@admin_required
def new():
    db = get_db()
    username = request.form["username"].strip()
    password = request.form["password"]
    role = request.form.get("role", "editor")

    error = None
    if not username or not password:
        error = "Username and password are required."
    elif role not in permissions.role_names(db):
        error = "Invalid role."

    if error is None:
        try:
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), role),
            )
            db.commit()
            flash(f"Added user {username}.", "success")
        except sqlite3.IntegrityError:
            error = f"Username '{username}' is already taken."

    if error:
        flash(error, "error")

    return redirect(url_for("users.list_users"))


@bp.route("/<int:user_id>")
@admin_required
def detail(user_id):
    db = get_db()
    user = _user_or_404(db, user_id)
    role_perms = permissions.role_permissions(db, user["role"])
    overrides = permissions.user_overrides(db, user_id)
    effective = permissions.effective_permissions(db, user_id, user["role"])
    return render_template(
        "users/detail.html",
        user=user, roles=permissions.role_names(db), groups=permissions.groups(),
        role_perms=role_perms, overrides=overrides, effective=effective,
        is_admin_role=user["role"] == permissions.ADMIN_ROLE,
    )


@bp.route("/<int:user_id>/role", methods=("POST",))
@admin_required
def set_role(user_id):
    db = get_db()
    user = _user_or_404(db, user_id)
    role = request.form.get("role", "")

    if role not in permissions.role_names(db):
        flash("Invalid role.", "error")
    elif role != user["role"] and _would_strand_admins(db, user):
        flash("You can't change the only admin's role -- make someone else an admin first.", "error")
    else:
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        db.commit()
        flash(f"{user['username']} is now {role}.", "success")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<int:user_id>/permissions", methods=("POST",))
@admin_required
def set_permissions(user_id):
    """Each permission arrives as perm__<key> = inherit | allow | deny."""
    db = get_db()
    user = _user_or_404(db, user_id)
    if user["role"] == permissions.ADMIN_ROLE:
        flash("Admins always have every permission.", "error")
        return redirect(url_for("users.detail", user_id=user_id))

    db.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
    for key in permissions.ALL_KEYS:
        choice = request.form.get(f"perm__{key}", "inherit")
        if choice in ("allow", "deny"):
            db.execute(
                "INSERT INTO user_permissions (user_id, permission, allowed) VALUES (?, ?, ?)",
                (user_id, key, 1 if choice == "allow" else 0),
            )
    db.commit()
    flash(f"Saved permissions for {user['username']}.", "success")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<int:user_id>/password", methods=("POST",))
@admin_required
def set_password(user_id):
    db = get_db()
    user = _user_or_404(db, user_id)
    password = request.form.get("password", "")
    if not password:
        flash("Enter a new password.", "error")
    elif password != request.form.get("confirm", ""):
        flash("The two passwords don't match.", "error")
    else:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
        db.commit()
        flash(f"Password changed for {user['username']}.", "success")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<int:user_id>/delete", methods=("POST",))
@admin_required
def delete(user_id):
    db = get_db()
    user = _user_or_404(db, user_id)
    if user_id == g.user["id"]:
        flash("You can't remove your own account.", "error")
    elif _would_strand_admins(db, user):
        flash("You can't remove the only admin.", "error")
    else:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        flash(f"Removed {user['username']}.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/roles")
@admin_required
def list_roles():
    db = get_db()
    roles = db.execute(
        """SELECT roles.name, roles.description, roles.is_builtin,
                  (SELECT COUNT(*) FROM users WHERE users.role = roles.name) AS user_count
           FROM roles ORDER BY roles.is_builtin DESC, roles.name"""
    ).fetchall()
    granted = {r["name"]: permissions.role_permissions(db, r["name"]) for r in roles}
    return render_template(
        "users/roles.html", roles=roles, granted=granted, groups=permissions.groups(),
        admin_role=permissions.ADMIN_ROLE,
    )


@bp.route("/roles/save", methods=("POST",))
@admin_required
def save_roles():
    """Each cell of the matrix arrives as rp__<role>__<permission> when ticked.
    The admin column is never saved -- admin always has everything."""
    db = get_db()
    for role in permissions.role_names(db):
        if role == permissions.ADMIN_ROLE:
            continue
        db.execute("DELETE FROM role_permissions WHERE role = ?", (role,))
        for key in permissions.ALL_KEYS:
            if request.form.get(f"rp__{role}__{key}"):
                db.execute(
                    "INSERT INTO role_permissions (role, permission) VALUES (?, ?)", (role, key)
                )
    db.commit()
    flash("Saved role permissions.", "success")
    return redirect(url_for("users.list_roles"))


@bp.route("/roles/new", methods=("POST",))
@admin_required
def new_role():
    db = get_db()
    name = request.form.get("name", "").strip().lower()
    description = request.form.get("description", "").strip() or None
    copy_from = request.form.get("copy_from", "")

    if not ROLE_NAME_RE.match(name):
        flash("Role names are 1-30 characters: lowercase letters, numbers, - and _.", "error")
    elif name in permissions.role_names(db):
        flash(f"A role named '{name}' already exists.", "error")
    else:
        db.execute("INSERT INTO roles (name, description, is_builtin) VALUES (?, ?, 0)", (name, description))
        if copy_from in permissions.role_names(db):
            for key in permissions.role_permissions(db, copy_from):
                db.execute("INSERT INTO role_permissions (role, permission) VALUES (?, ?)", (name, key))
        db.commit()
        flash(f"Added role {name}. Tick its permissions below.", "success")
    return redirect(url_for("users.list_roles"))


@bp.route("/roles/<role_name>/delete", methods=("POST",))
@admin_required
def delete_role(role_name):
    db = get_db()
    role = db.execute("SELECT name, is_builtin FROM roles WHERE name = ?", (role_name,)).fetchone()
    if role is None:
        abort(404)
    in_use = db.execute("SELECT COUNT(*) AS n FROM users WHERE role = ?", (role_name,)).fetchone()["n"]
    if role["is_builtin"]:
        flash("Built-in roles can't be deleted.", "error")
    elif in_use:
        flash(f"{in_use} user(s) still have the '{role_name}' role -- reassign them first.", "error")
    else:
        db.execute("DELETE FROM roles WHERE name = ?", (role_name,))
        db.commit()
        flash(f"Deleted role {role_name}.", "success")
    return redirect(url_for("users.list_roles"))
