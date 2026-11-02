"""Loopback-only dashboard with local sign-in for the encrypted event repo."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .usage_database import read_dashboard_data

PAGE = Path(__file__).with_name("dashboard.html")
HOST, PORT = "127.0.0.1", 8765
SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
MAX_LOGIN_REQUEST_BYTES = 16_384
MAX_LOGIN_PASSWORD_CHARS = 1024
_sessions: dict[str, float] = {}
_failed_logins: dict[str, tuple[int, float]] = {}


def _password_file() -> Path:
    configured = os.environ.get("OFFLINE_CHESS_DASHBOARD_PASSWORD_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "OfflineChess" / "dashboard-password.json"


def _load_password_record() -> dict | None:
    try:
        value = json.loads(_password_file().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        iterations = value.get("iterations")
        if value.get("algorithm") != "pbkdf2_sha256" or not isinstance(iterations, int) or not 100_000 <= iterations <= 2_000_000:
            return None
        salt = base64.b64decode(value["salt"], validate=True)
        digest = base64.b64decode(value["digest"], validate=True)
        if len(salt) != 16 or len(digest) != 32:
            return None
        return {"iterations": iterations, "salt": salt, "digest": digest}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "OfflineChessLocalDashboard/1"

    def _send(self, status: int, content_type: str, body: bytes, extra_headers: tuple[tuple[str, str], ...] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _loopback_host_ok(self) -> bool:
        host = self.headers.get("Host", "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _session_id(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, KeyError, ValueError):
            return None
        morsel = cookie.get("ochess_session")
        token = morsel.value if morsel else None
        expiry = _sessions.get(token, 0.0) if token else 0.0
        if expiry <= time.time():
            if token:
                _sessions.pop(token, None)
            return None
        return token

    def _require_session(self) -> bool:
        if self._session_id():
            return True
        self._send(401, "application/json; charset=utf-8", b'{"error":"Sign in to view this dashboard."}')
        return False

    def do_GET(self):
        if not self._loopback_host_ok():
            return self._send(403, "text/plain; charset=utf-8", b"Loopback only")
        if self.path == "/":
            try:
                body = PAGE.read_bytes()
            except OSError:
                return self._send(500, "text/plain; charset=utf-8", b"Dashboard page is missing")
            return self._send(200, "text/html; charset=utf-8", body)
        if self.path == "/api/dashboard":
            if not self._require_session():
                return
            try:
                body = json.dumps(read_dashboard_data(), ensure_ascii=False).encode("utf-8")
                return self._send(200, "application/json; charset=utf-8", body)
            except RuntimeError as exc:
                return self._send(503, "application/json; charset=utf-8",
                                  json.dumps({"error": str(exc)}).encode("utf-8"))
        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def do_POST(self):
        if not self._loopback_host_ok():
            return self._send(403, "text/plain; charset=utf-8", b"Loopback only")
        if self.path == "/api/login":
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host', '')}":
                return self._send(403, "application/json; charset=utf-8", b'{"error":"Invalid request origin."}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_LOGIN_REQUEST_BYTES:
                return self._send(400, "application/json; charset=utf-8", b'{"error":"Invalid login request."}')
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, ValueError):
                return self._send(400, "application/json; charset=utf-8", b'{"error":"Invalid login request."}')
            password = body.get("password") if isinstance(body, dict) else None
            if not isinstance(password, str) or not 1 <= len(password) <= MAX_LOGIN_PASSWORD_CHARS:
                return self._send(400, "application/json; charset=utf-8", b'{"error":"Enter your dashboard password."}')
            record = _load_password_record()
            if record is None:
                return self._send(503, "application/json; charset=utf-8", b'{"error":"Run setup_dashboard_login.py to configure dashboard sign-in."}')
            peer = self.client_address[0]
            now = time.time()
            failures, expiry = _failed_logins.get(peer, (0, 0.0))
            if expiry > now and failures >= LOGIN_MAX_FAILURES:
                return self._send(429, "application/json; charset=utf-8", b'{"error":"Too many attempts. Try again in 15 minutes."}')
            try:
                password_bytes = password.encode("utf-8")
            except UnicodeEncodeError:
                return self._send(400, "application/json; charset=utf-8", b'{"error":"Enter a valid dashboard password."}')
            candidate = hashlib.pbkdf2_hmac("sha256", password_bytes, record["salt"], record["iterations"])
            if not hmac.compare_digest(candidate, record["digest"]):
                if expiry <= now:
                    failures, expiry = 0, now + LOGIN_WINDOW_SECONDS
                _failed_logins[peer] = (failures + 1, expiry)
                return self._send(401, "application/json; charset=utf-8", b'{"error":"Password not accepted."}')
            _failed_logins.pop(peer, None)
            for token, expiration in list(_sessions.items()):
                if expiration <= now:
                    _sessions.pop(token, None)
            if len(_sessions) >= 64:
                _sessions.pop(next(iter(_sessions)))
            token = secrets.token_urlsafe(32)
            _sessions[token] = now + SESSION_SECONDS
            return self._send(200, "application/json; charset=utf-8", b'{"ok":true}', (
                ("Set-Cookie", f"ochess_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"),
            ))
        if self.path == "/api/logout":
            token = self._session_id()
            if token:
                _sessions.pop(token, None)
            return self._send(200, "application/json; charset=utf-8", b'{"ok":true}', (
                ("Set-Cookie", "ochess_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),
            ))
        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def log_message(self, _format, *_args):
        pass


def serve_local_dashboard() -> None:
    server = ThreadingHTTPServer((HOST, PORT), _DashboardHandler)
    server.daemon_threads = True
    print(f"Offline Chess dashboard: http://{HOST}:{PORT}")
    print("Only this computer can connect. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
