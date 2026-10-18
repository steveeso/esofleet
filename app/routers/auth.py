import functools

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import permissions
from ..db import get_db

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.perms = set()
    if user_id is None:
        g.user = None
    else:
        db = get_db()
        g.user = db.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if g.user is not None:
            g.perms = permissions.effective_permissions(db, g.user["id"], g.user["role"])


@bp.app_context_processor
def inject_can():
    def can(permission):
        return permission in getattr(g, "perms", ())

    return {"can": can}


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)

    return wrapped_view


def admin_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["role"] != "admin":
            flash("That page is only available to admins.", "error")
            return redirect(url_for("dashboard.index"))
        return view(**kwargs)

    return wrapped_view


def permission_required(permission, json=False):
    """Like login_required, but also needs `permission`. json=True is for
    fetch() endpoints, which get a JSON 403 instead of the HTML error page."""

    def decorator(view):
        @functools.wraps(view)
        def wrapped_view(**kwargs):
            if g.user is None:
                if json:
                    return {"ok": False, "error": "Not signed in."}, 401
                return redirect(url_for("auth.login", next=request.path))
            if permission not in g.perms:
                if json:
                    return {"ok": False, "error": "You don't have permission to do that."}, 403
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorator


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        error = None
        user = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            error = "Incorrect username or password."

        if error is None:
            session.clear()
            session["user_id"] = user["id"]
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)

        flash(error, "error")

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
