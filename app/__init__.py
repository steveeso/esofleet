import os
from datetime import date, datetime
from flask import Flask

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLEET_SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.path.join(app.instance_path, "fleet.sqlite"),
    )

    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)

    @app.template_filter("days_ago")
    def days_ago(value):
        """"N days ago" (or "today"/"1 day ago") for a "YYYY-MM-DD"-ish date
        string, for showing how stale a supplier stock snapshot is."""
        if not value:
            return None
        try:
            checked = datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        delta = (date.today() - checked).days
        if delta < 0:
            return None
        if delta == 0:
            return "today"
        if delta == 1:
            return "1 day ago"
        return f"{delta} days ago"

    from .routers import auth, catalog, dashboard, equipment, maintenance, users

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(equipment.bp)
    app.register_blueprint(maintenance.bp)
    app.register_blueprint(catalog.bp)
    app.register_blueprint(users.bp)

    return app
