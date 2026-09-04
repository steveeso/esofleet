import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from ..db import get_db
from .auth import admin_required

bp = Blueprint("users", __name__, url_prefix="/users")

ROLES = ["admin", "editor"]


@bp.route("/")
@admin_required
def list_users():
    db = get_db()
    users = db.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
    return render_template("users/list.html", users=users, roles=ROLES)


@bp.route("/new", methods=("POST",))
@admin_required
def new():
    username = request.form["username"].strip()
    password = request.form["password"]
    role = request.form.get("role", "editor")

    error = None
    if not username or not password:
        error = "Username and password are required."
    elif role not in ROLES:
        error = "Invalid role."

    if error is None:
        db = get_db()
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


@bp.route("/<int:user_id>/delete", methods=("POST",))
@admin_required
def delete(user_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash("User removed.", "success")
    return redirect(url_for("users.list_users"))
