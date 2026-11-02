"""Exercise desktop GUI entry points offscreen and record response timings.

Run with the project venv's Python on Windows. User data and fixture files are
kept in a temporary LOCALAPPDATA directory and removed when the run ends.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_profile = tempfile.TemporaryDirectory(prefix="offline-chess-gui-audit-", dir=ROOT)
os.environ["LOCALAPPDATA"] = _profile.name

import chess
import chess.pgn
from PyQt6.QtWidgets import QApplication, QAbstractButton, QComboBox, QFileDialog, QMessageBox, QMenu
from PyQt6.QtGui import QDesktopServices

from offline_chess.ui import main_window as main_window_module
from offline_chess.ui.main_window import ChessWindow
from offline_chess.ui.profile_dialog import ProfileDialog
from offline_chess.ui.feedback_dialog import FeedbackDialog


rows: list[dict[str, str]] = []
uncaught: list[str] = []
old_excepthook = sys.excepthook


def exception_hook(kind, value, tb):
    uncaught.append("".join(traceback.format_exception(kind, value, tb)))
    old_excepthook(kind, value, tb)


sys.excepthook = exception_hook
QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No)
QMessageBox.warning = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
QMessageBox.information = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
QMessageBox.critical = staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: ("", ""))
QFileDialog.getSaveFileName = staticmethod(lambda *args, **kwargs: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *args, **kwargs: "")
QMenu.exec = lambda *args, **kwargs: None
QDesktopServices.openUrl = staticmethod(lambda *args, **kwargs: True)
__import__("PyQt6.QtWidgets", fromlist=["QDialog"]).QDialog.exec = lambda self, *args: 0
FeedbackDialog.exec = lambda self, *args: 0


def pump(app, ms=20):
    deadline = time.perf_counter() + ms / 1000
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(.001)


def record(group, name, action, fn, settle_ms=12, details=""):
    start = time.perf_counter()
    status = "ok"
    try:
        fn()
        ui_ms = (time.perf_counter() - start) * 1000
        pump(app, settle_ms)
    except Exception as exc:
        ui_ms = (time.perf_counter() - start) * 1000
        status = "error"
        details = f"{type(exc).__name__}: {exc}"
    rows.append({"group": group, "entry_point": name, "action": action,
                 "ui_response_ms": f"{ui_ms:.1f}", "end_to_end_ms": "",
                 "status": status, "details": details})
    return len(rows) - 1


def wait_until(predicate, timeout=15):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        app.processEvents()
        if predicate():
            return (time.perf_counter() - start) * 1000
        time.sleep(.01)
    return None


app = QApplication.instance() or QApplication([])
startup = time.perf_counter()
window = ChessWindow()
constructed_ms = (time.perf_counter() - startup) * 1000
window.show()
ready_ms = wait_until(lambda: window.engine_ready, 25)
rows.append({"group": "startup", "entry_point": "Desktop window", "action": "construct + show + engine ready",
             "ui_response_ms": f"{constructed_ms:.1f}",
             "end_to_end_ms": f"{ready_ms:.1f}" if ready_ms is not None else "timeout",
             "status": "ok" if ready_ms is not None else "timeout",
             "details": f"engine_ready={window.engine_ready}; engine status={window.engine_status.text()}"})
catalog_ms = wait_until(lambda: bool(window.openings), 15)
rows.append({"group": "startup", "entry_point": "Opening catalog", "action": "async catalog available",
             "ui_response_ms": "0.0", "end_to_end_ms": f"{catalog_ms:.1f}" if catalog_ms is not None else "timeout",
             "status": "ok" if catalog_ms is not None else "timeout",
             "details": f"entries={len(window.openings)}"})

move_started = time.perf_counter()
window.board_widget.square_clicked.emit(chess.E2)
window.board_widget.square_clicked.emit(chess.E4)
move_response_ms = (time.perf_counter() - move_started) * 1000
reply_ms = wait_until(lambda: len(window.history) >= 2, 12)
legal = len(window.history) >= 2 and window.history[0] == chess.Move.from_uci("e2e4")
rows.append({"group": "Game window", "entry_point": "Chessboard", "action": "play e2-e4 and await Stockfish reply",
             "ui_response_ms": f"{move_response_ms:.1f}",
             "end_to_end_ms": f"{reply_ms:.1f}" if reply_ms is not None else "timeout",
             "status": "ok" if reply_ms is not None and legal else "error",
             "details": f"user move legal={legal}; plies after response={len(window.history)}"})
window.reset()
pump(app, 30)

# Exercise every top-level tab and every visible main-window button in each
# reachable mode. Current-state hidden/disabled controls are counted below.
tabs_type = __import__("PyQt6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget
main_seen: set[tuple[str, str]] = set()
for tabs in window.findChildren(tabs_type):
    for index in range(tabs.count()):
        label = tabs.tabText(index)
        record("Game window", f"tab: {label}", "select tab", lambda t=tabs, i=index: t.setCurrentIndex(i))
        if tabs is window.main_tabs and label == "Game":
            for mode in ("play", "review", "coach"):
                window.set_mode(mode)
                pump(app, 12)
                for button in window.findChildren(QAbstractButton):
                    if not button.isVisible():
                        continue
                    text = button.text().replace("&", "").strip() or button.accessibleName() or button.objectName() or "unnamed button"
                    identity = (text, button.objectName())
                    if identity in main_seen:
                        continue
                    main_seen.add(identity)
                    if button.isEnabled():
                        record("Game window", f"{mode}: {text}", "button click", button.click,
                               details=f"objectName={button.objectName()}")
                    else:
                        rows.append({"group": "Game window", "entry_point": f"{mode}: {text}", "action": "button click",
                                     "ui_response_ms": "", "end_to_end_ms": "", "status": "skipped",
                                     "details": "disabled in the current state"})
        elif tabs is window.main_tabs:
            pump(app, 12)
            for button in window.findChildren(QAbstractButton):
                if not button.isVisible():
                    continue
                text = button.text().replace("&", "").strip() or button.accessibleName() or button.objectName() or "unnamed button"
                identity = (text, button.objectName())
                if identity in main_seen:
                    continue
                main_seen.add(identity)
                if button.isEnabled():
                    record("Game window", f"{label}: {text}", "button click", button.click,
                           details=f"objectName={button.objectName()}")
                else:
                    rows.append({"group": "Game window", "entry_point": f"{label}: {text}", "action": "button click",
                                 "ui_response_ms": "", "end_to_end_ms": "", "status": "skipped",
                                 "details": "disabled in the current state"})
for combo in window.findChildren(QComboBox):
    if not combo.isVisible():
        continue
    label = combo.accessibleName() or combo.objectName() or combo.currentText()
    for index in range(combo.count()):
        item = combo.itemText(index)
        record("Game window", f"combo: {label}", f"select {item}", lambda c=combo, i=index: c.setCurrentIndex(i))

# Position Lab validates both valid and malformed input. Engine analysis is
# allowed to complete locally and is timed separately from the GUI response.
window.main_tabs.setCurrentWidget(window.position_lab)
lab = window.position_lab
record("Position Lab", "Reset position", "button click", lab.reset_button.click)
record("Position Lab", "Load valid FEN", "set starting FEN", lambda: (lab.fen_input.setText(chess.STARTING_FEN), lab.load_fen()))
record("Position Lab", "Load invalid FEN", "reject malformed FEN", lambda: (lab.fen_input.setText("not a fen"), lab.load_fen()))
record("Position Lab", "Clear position", "button click", lab.clear_button.click)
record("Position Lab", "Reset position", "restore start", lab.reset_button.click)
lab.time_choices[2].setChecked(True)
position_results = []
window.worker.position_analysis_done.connect(lambda *args: position_results.append(args))
analysis_started = time.perf_counter()
record("Position Lab", "Analyze position", "request Stockfish analysis", lab.analyze_button.click)
analysis_done = wait_until(lambda: bool(position_results), 12)
rows[-1]["end_to_end_ms"] = f"{(time.perf_counter()-analysis_started)*1000:.1f}"
valid = False
if position_results:
    payload = position_results[-1]
    signal_payload = payload[-1] if isinstance(payload[-1], dict) else {}
    analysis_payload = signal_payload.get("results", {})
    valid = all(isinstance(analysis_payload.get(side), list) and len(analysis_payload[side]) >= 1
                for side in ("White", "Black"))
rows[-1]["status"] = "ok" if analysis_done is not None and valid else "error"
rows[-1]["details"] = f"analysis completion observed={analysis_done is not None}; both sides have candidates={valid}"

# Profile is isolated. Exercise each tab, every button and combo value;
# dialogs, file choosers, desktop links, and network calls are stubbed.
profile_start = time.perf_counter()
profile = ProfileDialog(window.profile_store, window.open_saved_game, parent=window)
profile.show()
app.processEvents()
rows.append({"group": "Profile", "entry_point": "Profile dialog", "action": "construct",
             "ui_response_ms": f"{(time.perf_counter()-profile_start)*1000:.1f}", "end_to_end_ms": "",
             "status": "ok", "details": f"tabs={profile.tabs.count()}"})
for index in range(profile.tabs.count()):
    label = profile.tabs.tabText(index)
    record("Profile", f"tab: {label}", "select tab", lambda i=index: profile.tabs.setCurrentIndex(i))
    pump(app, 15)
    profile_seen: set[tuple[str, str]] = set()
    for button in profile.findChildren(QAbstractButton):
        label_button = button.text().replace("&", "").strip() or button.accessibleName() or button.objectName() or "unnamed button"
        identity = (label_button, button.objectName())
        if identity in profile_seen:
            continue
        profile_seen.add(identity)
        if not button.isEnabled():
            continue
        record("Profile", label_button, "button click", button.click, details=f"profile tab={label}; objectName={button.objectName()}")
    for combo in profile.findChildren(QComboBox):
        if not combo.isVisible():
            continue
        combo_name = combo.accessibleName() or combo.objectName() or combo.currentText()
        for option in range(combo.count()):
            item = combo.itemText(option)
            record("Profile", f"combo: {combo_name}", f"select {item}", lambda c=combo, i=option: c.setCurrentIndex(i))

# Correctness smoke: legal move interaction and PGN parsing/export paths use a
# temporary fixture only. File dialogs are supplied a local fixture in this case.
fixture = Path(_profile.name) / "audit.pgn"
game = chess.pgn.Game()
node = game.add_variation(chess.Move.from_uci("e2e4"))
node.add_variation(chess.Move.from_uci("e7e5"))
fixture.write_text(str(game), encoding="utf-8")
main_window_module.QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: (str(fixture), "PGN"))
record("Game data", "Import PGN", "load synthetic two-ply game", window.import_pgn)
details = f"plies={len(window.history)}; final_fen={window.board.fen()}"
rows[-1]["details"] = details
main_window_module.QFileDialog.getSaveFileName = staticmethod(lambda *args, **kwargs: (str(Path(_profile.name) / "export.pgn"), "PGN"))
record("Game data", "Export PGN", "write then parse exported PGN", window.export_pgn)
exported = Path(_profile.name) / "export.pgn"
if exported.is_file():
    parsed = chess.pgn.read_game(exported.open(encoding="utf-8"))
    rows[-1]["details"] = f"exported={exported.stat().st_size} bytes; reparsed={parsed is not None}; result={parsed.headers.get('Result') if parsed else ''}"

# Close cleanly while the opening loader is finished and worker is still alive.
window.close()
window.thread.wait(12000)
pump(app, 100)
rows.append({"group": "Lifecycle", "entry_point": "Close window", "action": "close event + worker shutdown",
             "ui_response_ms": "", "end_to_end_ms": "", "status": "ok" if not window.thread.isRunning() else "thread still running",
             "details": f"engine thread running={window.thread.isRunning()}"})

with (Path.cwd() / "GUI_ENTRYPOINT_TIMINGS.csv").open("w", newline="", encoding="utf-8-sig") as report:
    writer = csv.DictWriter(report, fieldnames=["group", "entry_point", "action", "ui_response_ms", "end_to_end_ms", "status", "details"])
    writer.writeheader()
    writer.writerows(rows)

print(f"rows={len(rows)} errors={sum(row['status']=='error' for row in rows)} skipped={sum(row['status']=='skipped' for row in rows)}")
print(f"engine_ready={window.engine_ready} openings={len(window.openings)} engine_thread_running={window.thread.isRunning()}")
if uncaught:
    print("UNCAUGHT QT EXCEPTIONS:")
    print("\n".join(uncaught))
    raise SystemExit(2)
if any(row["status"] in ("error", "timeout", "thread still running") for row in rows):
    raise SystemExit(1)
