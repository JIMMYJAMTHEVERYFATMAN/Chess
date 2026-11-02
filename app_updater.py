"""Release checking and safe replacement for the Windows one-file build."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PyQt6.QtCore import QThread, pyqtSignal


REPOSITORY = "JIMMYJAMTHEVERYFATMAN/Chess"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
APP_USER_AGENT = "OfflineChess-Updater"


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _github_asset_url(asset: dict) -> str:
    url = str(asset.get("browser_download_url", ""))
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("The release contains an unexpected download URL.")
    return url


def _request(url: str):
    request = Request(url, headers={"User-Agent": APP_USER_AGENT,
                                    "Accept": "application/vnd.github+json"})
    return urlopen(request, timeout=25)


class UpdateCheckWorker(QThread):
    update_available = pyqtSignal(object)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self):
        try:
            with _request(LATEST_RELEASE_URL) as response:
                release = json.loads(response.read().decode("utf-8"))
            if release.get("draft") or release.get("prerelease"):
                return
            current = _version_tuple(self.current_version)
            latest = _version_tuple(release.get("tag_name", ""))
            if current is None or latest is None or latest <= current:
                return
            assets = {asset.get("name"): asset for asset in release.get("assets", [])}
            exe = assets.get("OfflineChess.exe")
            checksum = assets.get("OfflineChess.exe.sha256")
            if not exe or not checksum:
                return
            release["_exe_url"] = _github_asset_url(exe)
            release["_checksum_url"] = _github_asset_url(checksum)
            self.update_available.emit(release)
        except Exception:
            # A failed check should not prevent the user from playing offline.
            return


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    downloaded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, release: dict, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self):
        version = str(self.release.get("tag_name", "latest"))
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        update_dir = base / "OfflineChess" / "updates"
        partial_path = update_dir / f"OfflineChess-{version}.exe.part"
        final_path = update_dir / f"OfflineChess-{version}.exe"
        checksum_path = update_dir / f"OfflineChess-{version}.sha256"
        try:
            update_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            request = Request(self.release["_exe_url"], headers={"User-Agent": APP_USER_AGENT})
            with urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0) or 0)
                done = 0
                with partial_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)

            with _request(self.release["_checksum_url"]) as response:
                checksum_text = response.read(4096).decode("ascii", errors="replace")
            match = re.match(r"\s*([a-fA-F0-9]{64})", checksum_text)
            if not match:
                raise ValueError("The release checksum is missing or invalid.")
            if digest.hexdigest().lower() != match.group(1).lower():
                raise ValueError("The downloaded update failed its SHA-256 check.")
            partial_path.replace(final_path)
            checksum_path.write_text(match.group(1).lower(), encoding="ascii")
            self.downloaded.emit(str(final_path))
        except Exception as exc:
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(f"Chess {version} could not be downloaded or verified.\n\n{exc}")


def launch_windows_update(downloaded_exe: str | Path, installed_exe: str | Path) -> None:
    """Start a detached PowerShell helper that swaps the EXE after this app exits."""
    if os.name != "nt":
        raise OSError("Automatic installation is currently supported on Windows only.")
    source = Path(downloaded_exe).resolve()
    target = Path(installed_exe).resolve()
    if not source.is_file() or source.stat().st_size < 1024 * 1024:
        raise ValueError("The downloaded application file is missing or incomplete.")
    if not target.is_file():
        raise ValueError("Could not locate the currently running application.")

    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = "\n".join((
        f"$processToWait = {os.getpid()}",
        f"$source = {ps_quote(str(source))}",
        f"$target = {ps_quote(str(target))}",
        "$next = $target + '.new'",
        "while (Get-Process -Id $processToWait -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 1 }",
        "try {",
        "  Copy-Item -LiteralPath $source -Destination $next -Force -ErrorAction Stop",
        "  Move-Item -LiteralPath $next -Destination $target -Force -ErrorAction Stop",
        "} catch {",
        "  Remove-Item -LiteralPath $next -Force -ErrorAction SilentlyContinue",
        "}",
        "Start-Process -FilePath $target",
        "Remove-Item -LiteralPath $source -Force -ErrorAction SilentlyContinue",
    ))
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                     creationflags=flags, close_fds=True)
