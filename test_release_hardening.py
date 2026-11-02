"""Release-critical edge cases for updates, telemetry, and the local dashboard."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import chess
import chess.pgn
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTableWidgetItem
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import app_updater
import setup_dashboard_login
from offline_chess import local_dashboard, telemetry, usage_database
from offline_chess.chesscom_data import (
    ChessComCastlingScanWorker,
    ChessComFetchWorker,
    ProfileStore,
    validate_chesscom_archive_url,
    validate_chesscom_country_url,
)
from offline_chess.review_logic import castling_timing_label
from offline_chess.ui.profile_dialog import ProfileDialog


class UpdateHardeningTests(unittest.TestCase):
    def test_version_parser_handles_tags_and_rejects_non_semver(self):
        self.assertEqual(app_updater._version_tuple("v1.0.0"), (1, 0, 0))
        self.assertEqual(app_updater._version_tuple("0.10.5"), (0, 10, 5))
        for invalid in ("", "1.0", "v1.0.0-rc1", "latest", "1.0.0/../x"):
            with self.subTest(version=invalid):
                self.assertIsNone(app_updater._version_tuple(invalid))

    def test_release_asset_urls_require_https_github_assets(self):
        self.assertEqual(app_updater._github_asset_url({
            "browser_download_url": "https://github.com/owner/repo/releases/download/v1/app.exe",
        }), "https://github.com/owner/repo/releases/download/v1/app.exe")
        for url in ("http://github.com/a", "https://example.com/a", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                app_updater._github_asset_url({"browser_download_url": url})

    def test_update_check_ignores_prereleases_stale_versions_and_bad_urls(self):
        cases = [
            {"tag_name": "v2.0.0", "prerelease": True},
            {"tag_name": "v0.10.4"},
            {"tag_name": "v1.0.0", "assets": [
                {"name": "OfflineChess.exe", "browser_download_url": "https://evil.example/app.exe"},
                {"name": "OfflineChess.exe.sha256", "browser_download_url": "https://github.com/a/sum"},
            ]},
        ]
        for release in cases:
            with self.subTest(release=release):
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = json.dumps(release).encode("utf-8")
                worker = app_updater.UpdateCheckWorker("0.10.5")
                found = []
                worker.update_available.connect(found.append)
                with patch.object(app_updater, "_request", return_value=response):
                    worker.run()
                self.assertEqual(found, [])

    def test_update_check_emits_only_a_new_release_with_both_assets(self):
        release = {"tag_name": "v1.0.0", "assets": [
            {"name": "OfflineChess.exe", "browser_download_url": "https://github.com/a/app.exe"},
            {"name": "OfflineChess.exe.sha256", "browser_download_url": "https://github.com/a/app.exe.sha256"},
        ]}
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(release).encode("utf-8")
        worker = app_updater.UpdateCheckWorker("0.10.5")
        found = []
        worker.update_available.connect(found.append)
        with patch.object(app_updater, "_request", return_value=response):
            worker.run()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["_exe_url"], "https://github.com/a/app.exe")
        self.assertEqual(found[0]["_checksum_url"], "https://github.com/a/app.exe.sha256")

    def test_download_worker_verifies_checksum_and_reports_progress(self):
        payload = b"verified executable payload"
        checksum = hashlib.sha256(payload).hexdigest()
        binary_response = MagicMock()
        binary_response.__enter__.return_value = binary_response
        binary_response.headers.get.return_value = str(len(payload))
        binary_response.read.side_effect = [payload, b""]
        checksum_response = MagicMock()
        checksum_response.__enter__.return_value = checksum_response
        checksum_response.read.return_value = f"{checksum}  OfflineChess.exe\n".encode("ascii")
        release = {
            "tag_name": "v1.0.0",
            "_exe_url": "https://github.com/example/app.exe",
            "_checksum_url": "https://github.com/example/app.exe.sha256",
        }
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as local_data:
            worker = app_updater.UpdateDownloadWorker(release)
            downloaded, progress, failed = [], [], []
            worker.downloaded.connect(downloaded.append)
            worker.progress.connect(lambda done, total: progress.append((done, total)))
            worker.failed.connect(failed.append)
            with patch.dict(os.environ, {"LOCALAPPDATA": local_data}), \
                 patch.object(app_updater, "urlopen", return_value=binary_response), \
                 patch.object(app_updater, "_request", return_value=checksum_response):
                worker.run()
            final = Path(local_data) / "OfflineChess" / "updates" / "OfflineChess-v1.0.0.exe"
            self.assertEqual(failed, [])
            self.assertEqual(len(downloaded), 1)
            self.assertEqual(Path(downloaded[0]), final)
            self.assertEqual(final.read_bytes(), payload)
            self.assertEqual((final.parent / "OfflineChess-v1.0.0.sha256").read_text(), checksum)
            self.assertEqual(progress, [(len(payload), len(payload))])

    def test_download_worker_discards_a_bad_checksum_download(self):
        binary_response = MagicMock()
        binary_response.__enter__.return_value = binary_response
        binary_response.headers.get.return_value = "3"
        binary_response.read.side_effect = [b"bad", b""]
        checksum_response = MagicMock()
        checksum_response.__enter__.return_value = checksum_response
        checksum_response.read.return_value = ("0" * 64).encode("ascii")
        release = {
            "tag_name": "v1.0.0",
            "_exe_url": "https://github.com/example/app.exe",
            "_checksum_url": "https://github.com/example/app.exe.sha256",
        }
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as local_data:
            worker = app_updater.UpdateDownloadWorker(release)
            failures = []
            worker.failed.connect(failures.append)
            with patch.dict(os.environ, {"LOCALAPPDATA": local_data}), \
                 patch.object(app_updater, "urlopen", return_value=binary_response), \
                 patch.object(app_updater, "_request", return_value=checksum_response):
                worker.run()
            update_dir = Path(local_data) / "OfflineChess" / "updates"
            self.assertEqual(len(failures), 1)
            self.assertFalse((update_dir / "OfflineChess-v1.0.0.exe.part").exists())
            self.assertFalse((update_dir / "OfflineChess-v1.0.0.exe").exists())

    @unittest.skipUnless(os.name == "nt", "Windows updater launch helper is Windows-only")
    def test_update_helper_quotes_paths_and_has_rollback_and_restart(self):
        with tempfile.TemporaryDirectory(prefix="updater's ", dir=os.getcwd()) as folder:
            source = Path(folder) / "download.exe"
            target = Path(folder) / "installed.exe"
            source.write_bytes(b"x" * (1024 * 1024))
            target.write_bytes(b"old executable")
            captured = {}

            def capture(args, **kwargs):
                captured["args"] = args

            with patch.object(app_updater.subprocess, "Popen", side_effect=capture):
                app_updater.launch_windows_update(source, target)
            args = captured["args"]
            script = base64.b64decode(args[-1]).decode("utf-16le")
            self.assertIn("$source = '" + str(source.resolve()).replace("'", "''") + "'", script)
            self.assertIn("$target = '" + str(target.resolve()).replace("'", "''") + "'", script)
            self.assertIn("$sourceHash -ne $nextHash", script)
            self.assertIn("$backup = $target + '.previous'", script)
            self.assertIn("WaitForInputIdle(15000)", script)
            self.assertIn("Reopened the existing app after update failure", script)

    @unittest.skipUnless(os.name == "nt", "PowerShell updater helper is Windows-only")
    def test_update_helper_swaps_and_reopens_or_restores_the_previous_executable(self):
        def run_helper(folder: Path, first_launch_ready: bool):
            source = folder / "download.exe"
            target = folder / "installed.exe"
            new_bytes = b"new build" * 150_000
            source.write_bytes(new_bytes)
            old_bytes = b"old build" * 150
            target.write_bytes(old_bytes)
            captured = {}
            with patch.object(app_updater.subprocess, "Popen", side_effect=lambda args, **_kwargs: captured.setdefault("args", args)):
                app_updater.launch_windows_update(source, target)
            script = base64.b64decode(captured["args"][-1]).decode("utf-16le")
            script = script.replace(f"$processToWait = {os.getpid()}", "$processToWait = 2147483000")
            script_path = folder / "helper.ps1"
            script_path.write_text(script, encoding="utf-8")
            q = lambda value: "'" + str(value).replace("'", "''") + "'"
            harness = "\n".join((
                "$script:StartCount = 0",
                "function Get-Process { [CmdletBinding()] param([int]$Id) return $null }",
                "function Start-Process {",
                "  [CmdletBinding()] param([string]$FilePath, [switch]$PassThru)",
                "  $script:StartCount++",
                f"  $ready = ($script:StartCount -gt 1) -or ${str(first_launch_ready).lower()}",
                "  $process = [pscustomobject]@{ Ready = $ready }",
                "  $process | Add-Member -MemberType ScriptMethod -Name WaitForInputIdle -Value { param($Timeout); return $this.Ready }",
                "  return $process",
                "}",
                f"& {q(script_path)}",
            ))
            result = app_updater.subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", harness],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return source, target, old_bytes, new_bytes, result

        with tempfile.TemporaryDirectory(prefix="updater-success-", dir=os.getcwd()) as folder:
            source, target, _old_bytes, new_bytes, _result = run_helper(Path(folder), True)
            self.assertEqual(target.read_bytes(), new_bytes,
                             (source.parent / "updater.log").read_text(encoding="utf-8"))
            self.assertFalse(source.exists())
            self.assertFalse(target.with_name(target.name + ".previous").exists())
            self.assertIn("reopened successfully", (source.parent / "updater.log").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory(prefix="updater-rollback-", dir=os.getcwd()) as folder:
            source, target, old_bytes, _new_bytes, _result = run_helper(Path(folder), False)
            self.assertEqual(target.read_bytes(), old_bytes)
            self.assertTrue(source.exists())
            self.assertFalse(target.with_name(target.name + ".previous").exists())
            log = (source.parent / "updater.log").read_text(encoding="utf-8")
            self.assertIn("Update failed", log)
            self.assertIn("Reopened the existing app after update failure", log)


class TelemetryAndDashboardDataTests(unittest.TestCase):
    def test_feedback_validation_trims_and_bounds_message(self):
        store = MagicMock()
        store.get_setting.side_effect = lambda key, default=None: {"usage_install_id": "install-1"}.get(key, default)
        result = telemetry.make_feedback(store, "bug", "  Unicode issue: ♟  ")
        self.assertEqual(result["message"], "Unicode issue: ♟")
        self.assertEqual(result["category"], "bug")
        for category, message in (("other", "message"), ("bug", "  "), ("feature", "x" * 3001)):
            with self.subTest(category=category, length=len(message)), self.assertRaises(ValueError):
                telemetry.make_feedback(store, category, message)

    def test_checkin_persists_install_id_and_contains_only_disclosed_fields(self):
        store = MagicMock()
        settings = {"chesscom_username": "Player_1"}
        store.get_setting.side_effect = lambda key, default=None: settings.get(key, default)
        value = telemetry.make_checkin(store)
        self.assertEqual(set(value), {"event_id", "install_id", "app_version", "last_seen", "chesscom_username"})
        self.assertEqual(value["chesscom_username"], "Player_1")
        self.assertEqual(value["install_id"], str(__import__("uuid").UUID(value["install_id"])))
        self.assertTrue(value["last_seen"].endswith("+00:00"))
        store.set_setting.assert_called_once_with("usage_install_id", value["install_id"])

    def test_encrypted_dashboard_events_aggregate_latest_install_and_skip_corruption(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        def encrypted_event(value):
            aes_key = os.urandom(32)
            nonce = os.urandom(12)
            ciphertext = AESGCM(aes_key).encrypt(nonce, json.dumps(value).encode("utf-8"), usage_database.AAD)
            wrapped = public_key.encrypt(
                aes_key,
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                             algorithm=hashes.SHA256(), label=None),
            )
            envelope = {
                "version": 1,
                "wrapped_key": base64.b64encode(wrapped).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            }
            return (usage_database.MAGIC + base64.b64encode(json.dumps(envelope).encode()).decode()).encode("ascii")

        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            root = Path(folder)
            (root / "events" / "checkins").mkdir(parents=True)
            (root / "events" / "feedback").mkdir(parents=True)
            (root / "events" / "checkins" / "old.enc").write_bytes(encrypted_event({
                "install_id": "same", "last_seen": "2026-01-01T00:00:00+00:00", "app_version": "0.9.0",
            }))
            (root / "events" / "checkins" / "new.enc").write_bytes(encrypted_event({
                "install_id": "same", "last_seen": "2026-02-01T00:00:00+00:00", "app_version": "1.0.0",
            }))
            (root / "events" / "checkins" / "bad.enc").write_bytes(b"not ciphertext")
            (root / "events" / "checkins" / "no-id.enc").write_bytes(encrypted_event({"last_seen": "2027"}))
            (root / "events" / "feedback" / "report.enc").write_bytes(encrypted_event({
                "category": "bug", "message": "It broke", "submitted_at": "2026-02-03T00:00:00+00:00",
            }))
            (root / "events" / "feedback" / "invalid-category.enc").write_bytes(encrypted_event({
                "category": "private", "message": "skip", "submitted_at": "2027",
            }))
            key_path = root / "dashboard.pem"
            key_path.write_bytes(private_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            with patch.dict(os.environ, {
                usage_database.REPOSITORY_PATH_ENV: folder,
                usage_database.PRIVATE_KEY_ENV: str(key_path),
            }), patch.object(usage_database, "_sync_repository"):
                result = usage_database.read_dashboard_data()
            self.assertEqual(len(result["installations"]), 1)
            self.assertEqual(result["installations"][0]["app_version"], "1.0.0")
            self.assertEqual(result["feedback"][0]["message"], "It broke")
            self.assertTrue(result["updated_at"].endswith("+00:00"))

    def test_encrypted_event_rejects_modified_ciphertext(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        aes_key, nonce = os.urandom(32), os.urandom(12)
        cipher = AESGCM(aes_key).encrypt(nonce, b'{"install_id":"x"}', usage_database.AAD)
        wrapped = private_key.public_key().encrypt(
            aes_key,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
        envelope = {"version": 1, "wrapped_key": base64.b64encode(wrapped).decode(),
                    "nonce": base64.b64encode(nonce).decode(),
                    "ciphertext": base64.b64encode(cipher[:-1] + bytes([cipher[-1] ^ 1])).decode()}
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            path = Path(folder) / "tampered.enc"
            path.write_text(usage_database.MAGIC + base64.b64encode(json.dumps(envelope).encode()).decode(), encoding="ascii")
            with self.assertRaises(Exception):
                usage_database._decrypt_file(path, private_key)

    def test_dashboard_password_setup_accepts_unicode_and_rejects_overlong_input(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            destination = Path(folder) / "dashboard-password.json"
            password = "♟correct horse"
            with patch.object(setup_dashboard_login, "password_path", return_value=destination), \
                 patch.object(setup_dashboard_login.getpass, "getpass", side_effect=[password, password]):
                self.assertEqual(setup_dashboard_login.main(), 0)
            record = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(record["algorithm"], "pbkdf2_sha256")
            self.assertEqual(len(base64.b64decode(record["salt"])), 16)
            self.assertEqual(len(base64.b64decode(record["digest"])), 32)

            destination.unlink()
            with patch.object(setup_dashboard_login, "password_path", return_value=destination), \
                 patch.object(setup_dashboard_login.getpass, "getpass", side_effect=["x" * 1025, "x" * 1025]):
                self.assertEqual(setup_dashboard_login.main(), 2)
            self.assertFalse(destination.exists())


class ChessComImportEdgeTests(unittest.TestCase):
    def test_archive_urls_reject_query_credentials_and_nonstandard_ports(self):
        valid = "https://api.chess.com/pub/player/Player_1/games/2026/10"
        self.assertEqual(validate_chesscom_archive_url(valid), valid)
        for url in (valid + "?redirect=example", valid + "#fragment",
                    "https://user@api.chess.com/pub/player/player1/games/2026/10",
                    "https://api.chess.com:444/pub/player/player1/games/2026/10"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_chesscom_archive_url(url)

    def test_incremental_update_filters_months_and_deduplicates_across_archives(self):
        archives = [
            "https://api.chess.com/pub/player/player1/games/2026/03",
            "https://api.chess.com/pub/player/player1/games/2026/01",
            "https://api.chess.com/pub/player/player1/games/2026/02",
        ]
        games_by_month = {
            "2026/02": [{"url": "known"}, {"url": "new-1"}, {"url": "new-1"}],
            "2026/03": [{"url": "new-1"}, {"url": "new-2"}],
        }
        calls = []

        def request(url):
            calls.append(url)
            if url.endswith("/archives"):
                return {"archives": archives}
            month = "/".join(url.split("/")[-2:])
            return {"games": games_by_month[month]}

        worker = ChessComFetchWorker(
            "update", "player1", known_game_ids={"known"}, minimum_archive_month="2026-02",
            request_func=request,
        )
        added, progress, finished, failed = [], [], [], []
        worker.month_downloaded.connect(lambda archive, games: added.append((archive, [g["url"] for g in games])))
        worker.progress.connect(lambda done, total: progress.append((done, total)))
        worker.all_finished.connect(lambda: finished.append(True))
        worker.failed.connect(failed.append)
        worker.run()
        self.assertEqual(failed, [])
        self.assertEqual([archive for archive, _ in added], [archives[2], archives[0]])
        self.assertEqual(added, [(archives[2], ["new-1"]), (archives[0], ["new-2"])])
        self.assertEqual(worker.new_games_added, 2)
        self.assertEqual(worker.archives_checked, 2)
        self.assertEqual(progress, [(0, 2), (1, 2), (2, 2)])
        self.assertEqual(finished, [True])
        self.assertNotIn(archives[1], calls)

    def test_cancelled_full_download_finishes_without_fetching_months(self):
        calls = []

        def request(url):
            calls.append(url)
            return {"archives": ["https://api.chess.com/pub/player/p1/games/2026/01"]}

        worker = ChessComFetchWorker("all", "player1", request_func=request)
        worker.cancel()
        finished, downloaded = [], []
        worker.all_finished.connect(lambda: finished.append(True))
        worker.month_downloaded.connect(lambda *_args: downloaded.append(True))
        worker.run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(finished, [True])
        self.assertEqual(downloaded, [])

    def test_opponent_country_lookup_caches_shared_country_and_validates_urls(self):
        self.assertEqual(validate_chesscom_country_url("https://api.chess.com/pub/country/GB"),
                         "https://api.chess.com/pub/country/GB")
        for url in ("http://api.chess.com/pub/country/GB", "https://example.com/pub/country/GB",
                    "https://api.chess.com/pub/country/GB?x=1", "https://user@api.chess.com/pub/country/GB",
                    "https://api.chess.com:444/pub/country/GB"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_chesscom_country_url(url)
        calls = []

        def request(url):
            calls.append(url)
            if "/player/" in url:
                return {"country": "https://api.chess.com/pub/country/GB"}
            return {"name": "United Kingdom"}

        worker = ChessComFetchWorker("countries", "player1", opponents=["opponent1", "opponent2"],
                                     request_func=request)
        countries, completed, failed = [], [], []
        worker.country_ready.connect(lambda name, country: countries.append((name, country)))
        worker.countries_finished.connect(lambda done, total: completed.append((done, total)))
        worker.failed.connect(failed.append)
        worker.run()
        self.assertEqual(failed, [])
        self.assertEqual(countries, [("opponent1", "United Kingdom"), ("opponent2", "United Kingdom")])
        self.assertEqual(completed, [(2, 2)])
        self.assertEqual(calls.count("https://api.chess.com/pub/country/GB"), 1)

    def test_castling_scan_handles_opening_middle_endgame_no_castle_and_bad_pgn(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
            root = Path(folder)
            opening = root / "opening.pgn"
            opening.write_text('[Event "opening"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. O-O *\n', encoding="utf-8")
            middle = root / "middle.pgn"
            middle_board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 13")
            middle_game = chess.pgn.Game.from_board(middle_board)
            middle_game.add_main_variation(middle_board.parse_san("O-O"))
            middle.write_text(middle_game.accept(chess.pgn.StringExporter()), encoding="utf-8")
            endgame = root / "endgame.pgn"
            endgame_board = chess.Board("4k3/8/8/8/8/8/8/4K2R w K - 0 25")
            endgame_game = chess.pgn.Game.from_board(endgame_board)
            endgame_game.add_main_variation(endgame_board.parse_san("O-O"))
            endgame.write_text(endgame_game.accept(chess.pgn.StringExporter()), encoding="utf-8")
            no_castle = root / "no-castle.pgn"
            no_castle.write_text('[Event "no castle"]\n\n1. e4 e5 *\n', encoding="utf-8")
            illegal = root / "illegal.pgn"
            illegal.write_text('[Event "illegal"]\n\n1. e4 e5 2. f4 e5 *\n', encoding="utf-8")
            worker = ChessComCastlingScanWorker([
                {"game_id": "opening", "pgn_path": str(opening)},
                {"game_id": "middle", "pgn_path": str(middle)},
                {"game_id": "endgame", "pgn_path": str(endgame)},
                {"game_id": "none", "pgn_path": str(no_castle)},
                {"game_id": "illegal", "pgn_path": str(illegal)},
                {"game_id": "missing", "pgn_path": str(root / "missing.pgn")},
            ])
            results = []
            worker.scan_done.connect(results.append)
            worker.run()
        by_id = {row[0]: row[1:] for row in results[0]}
        self.assertEqual(by_id["opening"][:2], ("Opening", 7))
        self.assertEqual(by_id["middle"][:2], ("Middlegame", 1))
        self.assertEqual(by_id["endgame"][:2], ("Endgame", 1))
        self.assertEqual(by_id["none"][:2], ("Did not castle", None))
        self.assertIn("invalid moves", by_id["illegal"][2])
        self.assertTrue(by_id["missing"][2])

    def test_castling_timing_labels_cover_move_boundaries_and_bad_data(self):
        expected = {1: "Opening · moves 1–2", 4: "Opening · moves 1–2", 5: "Opening · move 3",
                    8: "Opening · move 4", 10: "Opening · move 5", 12: "Opening · move 6",
                    13: "Opening · move 7+", 24: "Opening · move 7+"}
        for ply, label in expected.items():
            with self.subTest(ply=ply):
                self.assertEqual(castling_timing_label("Opening", ply), label)
        self.assertEqual(castling_timing_label("Middlegame", None), "Middlegame")
        self.assertEqual(castling_timing_label("Endgame", None), "Endgame")
        self.assertEqual(castling_timing_label("Opening", "bad"), "Opening · move unknown")

    def test_castling_scan_results_persist_and_reject_unknown_phase(self):
        previous = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
                with patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                    store = ProfileStore()
                    game_id = store.save_chesscom_game("player1", {
                        "url": "https://www.chess.com/game/live/scan-persistence",
                        "end_time": 1793450000,
                        "pgn": '[Event "Online"]\n[Date "2026.10.31"]\n[White "player1"]\n[Black "opponent"]\n[Result "*"]\n\n1. f4 d5 2. Nf3 *\n',
                        "white": {"username": "player1", "rating": 1500, "result": "win"},
                        "black": {"username": "opponent", "rating": 1480, "result": "checkmated"},
                    })
                    with self.assertRaises(ValueError):
                        store.set_chesscom_castling_scans([(game_id, "Opening", 5), (game_id, "Unknown", 6)])
                    self.assertEqual(store.chesscom_games("player1")[0]["castling_scanned"], 0)
                    store.set_chesscom_castling_scans([(game_id, "Opening", 5)])
                    record = store.chesscom_games("player1")[0]
                    self.assertEqual(record["castling_scanned"], 1)
                    self.assertEqual(record["castling_phase"], "Opening")
                    self.assertEqual(record["castling_ply"], 5)
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous


class ChessComProfileUiEdgeTests(unittest.TestCase):
    def test_castling_row_filters_games_and_clicking_again_clears_filter(self):
        app = QApplication.instance() or QApplication([])
        previous = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
                with patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                    store = ProfileStore()
                    dialog = ProfileDialog(store, lambda _game: None)
                    games = [
                        {"game_id": "opening-3a", "castling_scanned": 1, "castling_phase": "Opening", "castling_ply": 5},
                        {"game_id": "opening-3b", "castling_scanned": 1, "castling_phase": "Opening", "castling_ply": 6},
                        {"game_id": "opening-4", "castling_scanned": 1, "castling_phase": "Opening", "castling_ply": 7},
                        {"game_id": "midgame", "castling_scanned": 1, "castling_phase": "Middlegame", "castling_ply": 30},
                        {"game_id": "reviewed-opening", "reviewed": 1,
                         "review_json": json.dumps({"moves": [{"san": "O-O", "phase": "Opening", "ply": 5}]}),
                         "castling_scanned": 0},
                        {"game_id": "not-castled", "reviewed": 1,
                         "review_json": json.dumps({"moves": [{"san": "e4", "phase": "Opening", "ply": 1}]}),
                         "castling_scanned": 0},
                    ]
                    dialog._chesscom_all_games = games
                    dialog._chesscom_library_unfiltered_matches = games
                    dialog._chesscom_castling_phase_by_id = None
                    dialog.chesscom_castling.setRowCount(1)
                    row_item = QTableWidgetItem("Opening · move 3")
                    row_item.setData(Qt.ItemDataRole.UserRole, "Opening · move 3")
                    dialog.chesscom_castling.setItem(0, 0, row_item)
                    rendered = []
                    with patch.object(dialog, "_render_chesscom_library", side_effect=lambda rows: rendered.append(list(rows))):
                        dialog._filter_chesscom_library_by_castling(0)
                        self.assertEqual([g["game_id"] for g in dialog._chesscom_library_matches],
                                         ["opening-3a", "opening-3b", "reviewed-opening"])
                        self.assertEqual(dialog._chesscom_castling_filter, "Opening · move 3")
                        dialog._filter_chesscom_library_by_castling(0)
                        self.assertIsNone(dialog._chesscom_castling_filter)
                        self.assertEqual(dialog._chesscom_library_matches, games)
                    self.assertEqual(len(rendered), 2)
                    dialog.close()
                    dialog.deleteLater()
                    app.processEvents()
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous

    def test_chesscom_storage_rejects_partial_parse_of_illegal_pgn(self):
        previous = os.environ.get("LOCALAPPDATA")
        try:
            with tempfile.TemporaryDirectory(dir=os.getcwd()) as folder:
                with patch.dict(os.environ, {"LOCALAPPDATA": folder}):
                    store = ProfileStore()
                    game = {
                        "url": "https://www.chess.com/game/live/invalid-pgn",
                        "end_time": 1793450000,
                        "pgn": '[Event "Online"]\n[White "player1"]\n[Black "opponent"]\n[Result "*"]\n\n1. e4 e5 2. f4 e5 *\n',
                        "white": {"username": "player1", "rating": 1500, "result": "win"},
                        "black": {"username": "opponent", "rating": 1480, "result": "checkmated"},
                    }
                    with self.assertRaisesRegex(ValueError, "invalid moves"):
                        store.save_chesscom_game("player1", game)
                    self.assertEqual(store.chesscom_games("player1"), [])
                    self.assertEqual(list((Path(folder) / "OfflineChess" / "chesscom_games").glob("**/*.pgn")), [])
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous


class LocalDashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.addCleanup(self.folder.cleanup)
        password_path = Path(self.folder.name) / "password.json"
        password = "correct horse battery staple ♟"
        salt = b"s" * 16
        iterations = 100_000
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        password_path.write_text(json.dumps({
            "algorithm": "pbkdf2_sha256", "iterations": iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "digest": base64.b64encode(digest).decode("ascii"),
        }), encoding="utf-8")
        self.password = password
        self._password_patch = patch.object(local_dashboard, "_password_file", return_value=password_path)
        self._password_patch.start()
        self.addCleanup(self._password_patch.stop)
        local_dashboard._sessions.clear()
        local_dashboard._failed_logins.clear()
        self.server = local_dashboard.ThreadingHTTPServer(("127.0.0.1", 0), local_dashboard._DashboardHandler)
        self.addCleanup(self.server.server_close)
        self._port_patch = patch.object(local_dashboard, "PORT", self.server.server_address[1])
        self._port_patch.start()
        self.addCleanup(self._port_patch.stop)
        self.thread = __import__("threading").Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.thread.join(timeout=3)))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.cookies = __import__("http.cookiejar").cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, body=None, headers=None, client=None, raw_body=None):
        data = raw_body if raw_body is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {},
                                         method="POST" if data is not None else "GET")
        try:
            response = (client or self.client).open(request, timeout=5)
            return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            result = (exc.code, exc.read(), exc.headers)
            exc.close()
            return result

    def test_authentication_origin_rate_limit_and_logout(self):
        status, _, _ = self.request("/api/dashboard")
        self.assertEqual(status, 401)
        status, _, _ = self.request("/api/login", {"password": "wrong"})
        self.assertEqual(status, 401)
        for _ in range(local_dashboard.LOGIN_MAX_FAILURES - 1):
            status, _, _ = self.request("/api/login", {"password": "wrong"})
            self.assertEqual(status, 401)
        status, _, _ = self.request("/api/login", {"password": self.password})
        self.assertEqual(status, 429)
        local_dashboard._failed_logins.clear()
        status, _, headers = self.request("/api/login", {"password": self.password})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        with patch.object(local_dashboard, "read_dashboard_data", return_value={"installations": [], "feedback": []}):
            status, body, response_headers = self.request("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["installations"], [])
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        status, _, _ = self.request("/api/login", {"password": self.password}, {"Origin": "http://attacker.invalid"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/logout", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/dashboard")[0], 401)

    def test_login_host_check_malformed_body_and_password_size_limit(self):
        self.assertEqual(self.request("/", headers={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/login", raw_body=b"{")[0], 400)
        self.assertEqual(self.request("/api/login", raw_body=b"x" * (local_dashboard.MAX_LOGIN_REQUEST_BYTES + 1))[0], 400)
        self.assertEqual(self.request("/api/login", {"password": "x" * (local_dashboard.MAX_LOGIN_PASSWORD_CHARS + 1)})[0], 400)


if __name__ == "__main__":
    unittest.main()
