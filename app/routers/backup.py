import csv
import io
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, send_file, url_for

from ..db import get_db
from .auth import admin_required

bp = Blueprint("backup", __name__, url_prefix="/backup")

# Server-side snapshots are only ever files this module named, so anything
# that doesn't match is refused -- that's what keeps a crafted filename from
# reaching outside instance/backups/.
SNAPSHOT_RE = re.compile(r"^esofleet-(production|test)-\d{8}-\d{6}\.sqlite$")

# Left out of the spreadsheet export. Still in the full .sqlite backup,
# which has to be complete to be restorable.
CSV_EXCLUDED_COLUMNS = {("users", "password_hash")}


def _backup_dir():
    path = current_app.config["BACKUP_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def _stamp():
    return f"esofleet-{current_app.config['FLEET_ENV']}-{datetime.now():%Y%m%d-%H%M%S}"


def _copy_database(dest_path):
    """A consistent copy of the live database via SQLite's backup API --
    safe while other requests are reading or writing, unlike copying the
    file, which can catch it halfway through a write."""
    dest = sqlite3.connect(dest_path)
    try:
        get_db().backup(dest)
    finally:
        dest.close()


def _snapshots():
    out = []
    for name in sorted(os.listdir(_backup_dir()), reverse=True):
        if SNAPSHOT_RE.match(name):
            stat = os.stat(os.path.join(_backup_dir(), name))
            out.append({
                "name": name,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime),
            })
    return out


def _snapshot_path_or_404(name):
    if not SNAPSHOT_RE.match(name):
        abort(404)
    path = os.path.join(_backup_dir(), name)
    if not os.path.isfile(path):
        abort(404)
    return path


@bp.app_template_filter("filesize")
def filesize(num):
    for unit in ("bytes", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "bytes" else f"{num:.1f} {unit}"
        num /= 1024


@bp.route("/")
@admin_required
def index():
    db = get_db()
    tables = [
        {"name": r["name"], "rows": db.execute(f'SELECT COUNT(*) AS n FROM "{r["name"]}"').fetchone()["n"]}
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return render_template(
        "backup/index.html",
        tables=tables,
        snapshots=_snapshots(),
        db_size=os.path.getsize(current_app.config["DATABASE"]),
    )


@bp.route("/download", methods=("POST",))
@admin_required
def download():
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        _copy_database(tmp)
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        os.remove(tmp)
    return Response(
        data,
        mimetype="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{_stamp()}.sqlite"'},
    )


@bp.route("/export.zip", methods=("POST",))
@admin_required
def export_csv():
    """Every table as its own CSV, zipped -- for opening in a spreadsheet,
    not for restoring (the .sqlite download is the real backup)."""
    db = get_db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall():
            table = t["name"]
            cur = db.execute(f'SELECT * FROM "{table}"')
            columns = [d[0] for d in cur.description]
            keep = [i for i, c in enumerate(columns) if (table, c) not in CSV_EXCLUDED_COLUMNS]
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow([columns[i] for i in keep])
            for row in cur:
                writer.writerow([row[i] for i in keep])
            # utf-8-sig so Excel opens accented names correctly.
            zf.writestr(f"{table}.csv", out.getvalue().encode("utf-8-sig"))
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_stamp()}-csv.zip"'},
    )


@bp.route("/snapshots", methods=("POST",))
@admin_required
def create_snapshot():
    name = f"{_stamp()}.sqlite"
    path = os.path.join(_backup_dir(), name)
    if os.path.exists(path):
        flash("A snapshot was just saved -- wait a second and try again.", "error")
    else:
        _copy_database(path)
        flash(f"Saved snapshot {name}.", "success")
    return redirect(url_for("backup.index"))


@bp.route("/snapshots/<name>")
@admin_required
def download_snapshot(name):
    return send_file(_snapshot_path_or_404(name), as_attachment=True, download_name=name)


@bp.route("/snapshots/<name>/delete", methods=("POST",))
@admin_required
def delete_snapshot(name):
    os.remove(_snapshot_path_or_404(name))
    flash(f"Deleted snapshot {name}.", "success")
    return redirect(url_for("backup.index"))
