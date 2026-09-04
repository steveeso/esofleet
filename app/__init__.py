import os
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

    from .routers import auth, dashboard, vehicles, maintenance, users

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(vehicles.bp)
    app.register_blueprint(maintenance.bp)
    app.register_blueprint(users.bp)

    return app
