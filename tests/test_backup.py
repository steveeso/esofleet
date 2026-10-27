import csv
import io
import os
import sqlite3
import zipfile

from .conftest import login


def test_admin_only(client):
    assert "/auth/login" in client.get("/backup/").headers["Location"]
    login(client, "editor")
    for method, url in (("get", "/backup/"), ("post", "/backup/download"),
                        ("post", "/backup/export.zip"), ("post", "/backup/snapshots")):
        resp = getattr(client, method)(url)
        assert resp.status_code == 302 and "/backup" not in resp.headers["Location"], url


def test_download_is_a_complete_restorable_database(client, tmp_path):
    login(client, "admin")
    client.post("/suppliers/new", data={"name": "Bytown Diesel"})
    resp = client.post("/backup/download")
    assert resp.status_code == 200
    assert 'filename="esofleet-production-' in resp.headers["Content-Disposition"]
    path = tmp_path / "copy.sqlite"
    path.write_bytes(resp.data)
    db = sqlite3.connect(path)
    assert db.execute("SELECT name FROM suppliers").fetchall() == [("Bytown Diesel",)]
    assert db.execute("SELECT COUNT(*) FROM users WHERE password_hash != ''").fetchone()[0] == 3
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_csv_export_leaves_out_password_hashes(client):
    login(client, "admin")
    resp = client.post("/backup/export.zip")
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert "equipment.csv" in zf.namelist() and "suppliers.csv" in zf.namelist()
    rows = list(csv.reader(io.StringIO(zf.read("users.csv").decode("utf-8-sig"))))
    assert "password_hash" not in rows[0] and "username" in rows[0]
    assert sorted(r[rows[0].index("username")] for r in rows[1:]) == ["admin", "editor", "viewer"]
    assert b"pbkdf2" not in zf.read("users.csv") and b"scrypt" not in zf.read("users.csv")


def test_snapshots(app, client):
    login(client, "admin")
    assert client.post("/backup/snapshots").status_code == 302
    backups = app.config["BACKUP_DIR"]
    names = os.listdir(backups)
    assert len(names) == 1
    name = names[0]
    assert name in client.get("/backup/").get_data(as_text=True)

    resp = client.get(f"/backup/snapshots/{name}")
    assert resp.status_code == 200 and resp.data[:16] == b"SQLite format 3\x00"
    resp.close()

    # Only names this tool made are ever served or deleted.
    assert client.get("/backup/snapshots/..%2Ffleet.sqlite").status_code == 404
    assert client.post("/backup/snapshots/fleet.sqlite/delete").status_code == 404

    client.post(f"/backup/snapshots/{name}/delete")
    assert os.listdir(backups) == []


def test_users_tab_links_to_backup(client):
    login(client, "admin")
    assert b'href="/backup/"' in client.get("/users/").data
