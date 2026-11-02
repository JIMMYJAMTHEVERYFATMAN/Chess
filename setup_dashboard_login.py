"""Set or replace the local password used by the localhost dashboard."""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

ITERATIONS = 600_000


def password_path() -> Path:
    configured = os.environ.get("OFFLINE_CHESS_DASHBOARD_PASSWORD_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "OfflineChess" / "dashboard-password.json"


def main() -> int:
    destination = password_path()
    if destination.exists() and input(f"Replace the existing dashboard password at {destination}? [y/N] ").strip().lower() != "y":
        print("Password unchanged.")
        return 1
    password = getpass.getpass("Choose a dashboard password (12 to 1,024 characters): ")
    if not 12 <= len(password) <= 1024:
        print("Use 12 to 1,024 characters.", file=sys.stderr)
        return 2
    confirm = getpass.getpass("Confirm dashboard password: ")
    if not secrets.compare_digest(password.encode("utf-8"), confirm.encode("utf-8")):
        print("Passwords did not match.", file=sys.stderr)
        return 2
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    record = {
        "algorithm": "pbkdf2_sha256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(record, output)
            output.write("\n")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Local dashboard sign-in configured at {destination}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
