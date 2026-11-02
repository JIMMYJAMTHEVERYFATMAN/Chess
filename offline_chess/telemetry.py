"""Minimal, disclosed installation check-ins and user feedback."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from .config import APP_VERSION, USAGE_REPORT_URL

_EVENT_ENDPOINTS = {
    "checkin": "/api/checkin",
    "feedback": "/api/feedback",
}
FEEDBACK_RATE_SETTING = "feedback_send_timestamps"
FEEDBACK_DAILY_LIMIT = 5
FEEDBACK_COOLDOWN_SECONDS = 60
FEEDBACK_WINDOW_SECONDS = 24 * 60 * 60


def ensure_install_id(store):
    value = store.get_setting("usage_install_id")
    if not value:
        value = str(uuid.uuid4())
        store.set_setting("usage_install_id", value)
    return value


def _post(path: str, payload: dict) -> None:
    if not USAGE_REPORT_URL.startswith("https://"):
        raise OSError("Usage reporting service is not configured")
    endpoint = _EVENT_ENDPOINTS.get(path)
    if endpoint is None:
        raise ValueError("Unknown usage reporting event")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{USAGE_REPORT_URL.rstrip('/')}{endpoint}", data=data,
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
    now = datetime.now(timezone.utc).timestamp()
    try:
        saved = json.loads(store.get_setting(FEEDBACK_RATE_SETTING, "[]") or "[]")
        timestamps = [float(value) for value in saved
                      if isinstance(value, (int, float)) and 0 <= now - float(value) < FEEDBACK_WINDOW_SECONDS]
    except (TypeError, ValueError):
        timestamps = []
    if timestamps and now - max(timestamps) < FEEDBACK_COOLDOWN_SECONDS:
        wait_seconds = FEEDBACK_COOLDOWN_SECONDS - int(now - max(timestamps))
        raise ValueError(f"Please wait {wait_seconds} seconds before sending another message.")
    if len(timestamps) >= FEEDBACK_DAILY_LIMIT:
        raise ValueError("You’ve reached the limit of 5 feedback messages per 24 hours.")
    timestamps.append(now)
    store.set_setting(FEEDBACK_RATE_SETTING, json.dumps(timestamps, separators=(",", ":")))
    return {
        "event_id": str(uuid.uuid4()),
        "install_id": ensure_install_id(store),
        "app_version": APP_VERSION,
        "chesscom_username": store.get_setting("chesscom_username", "") or None,
        "category": category,
        "message": message,
    }
