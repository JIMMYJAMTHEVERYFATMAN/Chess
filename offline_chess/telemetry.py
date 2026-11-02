"""Minimal, disclosed installation check-ins and user feedback."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from .config import APP_VERSION, USAGE_REPORT_URL


def ensure_install_id(store):
    value = store.get_setting("usage_install_id")
    if not value:
        value = str(uuid.uuid4())
        store.set_setting("usage_install_id", value)
    return value


def _post(path: str, payload: dict) -> None:
    if not USAGE_REPORT_URL.startswith("https://"):
        raise OSError("Usage reporting service is not configured")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{USAGE_REPORT_URL.rstrip('/')}/{path.lstrip('/')}", data=data,
        headers={"Content-Type": "application/json", "User-Agent": f"OfflineChess/{APP_VERSION}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        if response.status < 200 or response.status >= 300:
            raise urllib.error.HTTPError(request.full_url, response.status, "Request failed", response.headers, None)


def post_usage_event(path: str, payload: dict) -> None:
    """Send one disclosed event to the public write-only collector."""
    _post(path, payload)


class ReportingWorker(QThread):
    finished_with_result = pyqtSignal(bool, str)

    def __init__(self, path: str, payload: dict, parent=None):
        super().__init__(parent)
        self.path = path
        self.payload = payload

    def run(self):
        try:
            _post(self.path, self.payload)
            self.finished_with_result.emit(True, "")
        except (OSError, ValueError, TypeError) as exc:
            self.finished_with_result.emit(False, str(exc))


def make_checkin(store):
    """Build only the four explicitly disclosed install fields."""
    return {
        "event_id": str(uuid.uuid4()),
        "install_id": ensure_install_id(store),
        "app_version": APP_VERSION,
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chesscom_username": store.get_setting("chesscom_username", "") or None,
    }


def make_feedback(store, category: str, message: str):
    if category not in ("bug", "feature"):
        raise ValueError("Feedback category must be bug or feature.")
    message = str(message).strip()
    if not message or len(message) > 3000:
        raise ValueError("Please enter a message of 1 to 3,000 characters.")
    return {
        "event_id": str(uuid.uuid4()),
        "install_id": ensure_install_id(store),
        "app_version": APP_VERSION,
        "chesscom_username": store.get_setting("chesscom_username", "") or None,
        "category": category,
        "message": message,
    }
