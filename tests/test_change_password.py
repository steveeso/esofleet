from werkzeug.security import check_password_hash

from app.db import get_db

from .conftest import login


def _hash_ok(app, username, password):
    with app.app_context():
        row = get_db().execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        return check_password_hash(row["password_hash"], password)


def _change(client, current="pw", new="fresh-one", confirm=None):
    return client.post("/auth/password", data={
        "current": current, "password": new, "confirm": new if confirm is None else confirm,
    }, follow_redirects=True)


def test_needs_login(client):
    assert client.get("/auth/password").status_code == 302
    assert client.post("/auth/password", data={"current": "pw", "password": "x", "confirm": "x"}).status_code == 302


def test_any_role_can_change_own_password(app, client):
    for role in ("viewer", "editor", "admin"):
        login(client, role)
        assert client.get("/auth/password").status_code == 200
        resp = _change(client)
        assert b"Password changed." in resp.data
        assert _hash_ok(app, role, "fresh-one") and not _hash_ok(app, role, "pw")
        client.get("/auth/logout")


def test_new_password_works_for_next_login(app, client):
    login(client, "editor")
    _change(client)
    client.get("/auth/logout")
    assert client.post("/auth/login", data={"username": "editor", "password": "pw"}).status_code == 200
    resp = client.post("/auth/login", data={"username": "editor", "password": "fresh-one"})
    assert resp.status_code == 302


def test_wrong_current_password_changes_nothing(app, client):
    login(client, "editor")
    resp = _change(client, current="nope")
    assert b"current password isn" in resp.data
    assert _hash_ok(app, "editor", "pw")


def test_mismatch_empty_and_unchanged_are_rejected(app, client):
    login(client, "editor")
    assert b"don&#39;t match" in _change(client, new="abc", confirm="abd").data
    assert b"Enter a new password" in _change(client, new="").data
    assert b"different from your current" in _change(client, new="pw").data
    assert _hash_ok(app, "editor", "pw")


def test_only_changes_own_account(app, client):
    login(client, "editor")
    _change(client)
    assert _hash_ok(app, "viewer", "pw") and _hash_ok(app, "admin", "pw")


def test_sidebar_links_to_page(client):
    login(client, "viewer")
    assert b'href="/auth/password"' in client.get("/").data
