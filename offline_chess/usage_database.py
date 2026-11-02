"""Read the encrypted Offline Chess telemetry repository for localhost only."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidTag

MAGIC = "OCHESS1:"
AAD = b"OCHESS-TELEMETRY-v1"
REPOSITORY_PATH_ENV = "OFFLINE_CHESS_DATA_REPO"
PRIVATE_KEY_ENV = "OFFLINE_CHESS_DASHBOARD_PRIVATE_KEY"


def _private_key_from_environment():
    path = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    if not path:
        raise RuntimeError(f"Set {PRIVATE_KEY_ENV} to the local dashboard private-key PEM file.")
    try:
        return serialization.load_pem_private_key(Path(path).expanduser().read_bytes(), password=None)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not load the local dashboard private key: {exc}") from exc


def _data_repository() -> Path:
    path = os.environ.get(REPOSITORY_PATH_ENV, "").strip()
    if not path:
        raise RuntimeError(f"Set {REPOSITORY_PATH_ENV} to the local clone of the private telemetry repository.")
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise RuntimeError(f"Telemetry repository folder does not exist: {directory}")
    return directory


def _sync_repository(repository: Path) -> None:
    """Refresh the local private-repo clone without allowing prompts or merges."""
    if not (repository / ".git").exists():
        raise RuntimeError("The configured telemetry folder must be a Git clone.")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=repository, env=env,
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not refresh the private telemetry repository: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Could not refresh the private telemetry repository: {detail or 'git pull failed'}")


def _decrypt_file(path: Path, private_key) -> dict:
    text = path.read_text(encoding="ascii").strip()
    if not text.startswith(MAGIC):
        raise ValueError("Unknown encrypted event format")
    envelope = json.loads(base64.b64decode(text[len(MAGIC):], validate=True).decode("utf-8"))
    if envelope.get("version") != 1:
        raise ValueError("Encrypted event is incomplete")
    aes_key = private_key.decrypt(
        base64.b64decode(envelope["wrapped_key"], validate=True),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    if len(aes_key) != 32:
        raise ValueError("Encrypted event has an invalid AES-256 key")
    clear = AESGCM(aes_key).decrypt(
        base64.b64decode(envelope["nonce"], validate=True),
        base64.b64decode(envelope["ciphertext"], validate=True), AAD,
    )
    value = json.loads(clear.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Encrypted event payload is not an object")
    return value


def read_dashboard_data() -> dict:
    """Aggregate check-ins by install ID and list user-submitted feedback."""
    repository = _data_repository()
    _sync_repository(repository)
    private_key = _private_key_from_environment()
    installs: dict[str, dict] = {}
    reports: list[dict] = []
    for path in (repository / "events" / "checkins").glob("*.enc"):
        try:
            row = _decrypt_file(path, private_key)
            install_id = row.get("install_id")
            if not isinstance(install_id, str):
                continue
            prior = installs.get(install_id)
            if prior is None or str(row.get("last_seen", "")) > str(prior.get("last_seen", "")):
                installs[install_id] = row
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, InvalidTag):
            continue
    for path in (repository / "events" / "feedback").glob("*.enc"):
        try:
            row = _decrypt_file(path, private_key)
            if row.get("category") in ("bug", "feature") and isinstance(row.get("message"), str):
                reports.append(row)
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, InvalidTag):
            continue
    installations = sorted(installs.values(), key=lambda item: str(item.get("last_seen", "")), reverse=True)
    reports.sort(key=lambda item: str(item.get("submitted_at", "")), reverse=True)
    return {"installations": installations, "feedback": reports,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
