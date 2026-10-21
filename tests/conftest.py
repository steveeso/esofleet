import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fleet.sqlite")})
    with app.app_context():
        db = get_db()
        for username, role in (("admin", "admin"), ("editor", "editor"), ("viewer", "viewer")):
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, generate_password_hash("pw"), role),
            )
        db.commit()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username):
    resp = client.post("/auth/login", data={"username": username, "password": "pw"})
    assert resp.status_code == 302
    return client
