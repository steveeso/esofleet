import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug mode enables the Werkzeug interactive debugger, which lets
    # anyone who can reach an unhandled exception run arbitrary Python —
    # fine on localhost, dangerous the moment this is reachable by anyone
    # else (e.g. over Tailscale). Off by default; opt in for local dev with
    # FLASK_DEBUG=1.
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
