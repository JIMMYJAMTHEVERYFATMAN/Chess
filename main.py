"""Offline Chess — PyQt6 front end for Stockfish with local Lichess openings.

Install: pip install PyQt6 python-chess
Place Stockfish 19 beside this file, or set STOCKFISH_PATH.
For opening names, download Lichess chess-openings and put a.tsv through e.tsv
in an ``openings`` folder beside this file (or use Load Lichess DB in the app).
Optional WAV files may be placed in ``assets/{move,capture,check,castle,game_end}.wav``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import queue
import random
import re
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import wave
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    import chess
    import chess.engine
    import chess.pgn
    import chess.polyglot
except ImportError as exc:
    raise SystemExit("Install python-chess first: pip install python-chess") from exc

try:
    from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, pyqtSlot, QObject, QPointF, QRectF, QTimer
    from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QAction, QPolygonF, QDesktopServices
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QSlider, QDialog,
        QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QTableWidget,
        QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QProgressBar,
        QCheckBox, QSpinBox, QComboBox, QAbstractItemView, QScrollArea, QSizePolicy,
        QStackedWidget, QToolTip, QMenu,
        QTabWidget, QLineEdit,
    )
    try:
        from PyQt6.QtMultimedia import QSoundEffect
    except ImportError:
        QSoundEffect = None
    try:
        from PyQt6.QtTextToSpeech import QTextToSpeech
    except ImportError:
        QTextToSpeech = None
except ImportError as exc:
    raise SystemExit("Install PyQt6 first: pip install PyQt6") from exc

from app_updater import UpdateCheckWorker, UpdateDownloadWorker, launch_windows_update


APP_VERSION = "0.10.2"
IS_FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else BUNDLE_DIR
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", str(BUNDLE_DIR / ("stockfish.exe" if os.name == "nt" else "stockfish")))
MIN_ELO, MAX_ELO = 1320, 3190  # Stockfish's supported UCI_Elo range.
MIN_TARGET_ELO = 400           # Lower targets are approximated with weaker move choices.
NEAR_BEST_EXPECTED_POINTS_TOLERANCE = .01
BRILLIANT_MAX_PREMOVE_EXPECTED_POINTS = .98  # Avoid labeling a sacrifice in an already overwhelming position.
BRILLIANT_MIN_POSTMOVE_EXPECTED_POINTS = .55
BRILLIANT_MAX_EXPECTED_POINTS_LOSS = .02
BRILLIANT_MIN_REPORTED_GAP = .04
BRILLIANT_MIN_UNIQUENESS_GAP = .05
REVIEW_ANALYSIS_VERSION = 13
CHESSCOM_BATCH_REVIEW_BUDGET = .08
ACCURACY_CALIBRATION_EXPONENT = 5.0
ACCURACY_MODEL_VERSION = "caps2-inspired-v1"
GREAT_ONLY_MAX_PREMOVE_EXPECTED_POINTS = .75
GREAT_ONLY_MIN_POSTMOVE_EXPECTED_POINTS = .50
GREAT_ONLY_MIN_ALTERNATIVE_GAP = .15
GLYPHS = {"K":"♔", "Q":"♕", "R":"♖", "B":"♗", "N":"♘", "P":"♙", "k":"♚", "q":"♛", "r":"♜", "b":"♝", "n":"♞", "p":"♟"}
PIECE_LETTERS={chess.KING:"K",chess.QUEEN:"Q",chess.ROOK:"R",chess.BISHOP:"B",chess.KNIGHT:"N",chess.PAWN:"P"}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
BOARD_THEMES = {
    "classic_green": {"name":"Classic Green", "light":"#eeeed2", "dark":"#769656", "last":"#baca44", "selected":"#f6f669", "accent":"#268b5a"},
    "walnut": {"name":"Walnut", "light":"#f0d9b5", "dark":"#b58863", "last":"#d0ce63", "selected":"#fff080", "accent":"#268b5a"},
    "ocean": {"name":"Ocean Blue", "light":"#e6edf3", "dark":"#6689a8", "last":"#d4c961", "selected":"#fff080", "accent":"#218b86"},
    "violet": {"name":"Soft Violet", "light":"#eee8f5", "dark":"#8c77a8", "last":"#d4c86c", "selected":"#fff080", "accent":"#398b8b"},
    "slate": {"name":"Slate", "light":"#dce3e9", "dark":"#667b8f", "last":"#d4c86c", "selected":"#fff080", "accent":"#398b8b"},
    "mint": {"name":"Mint", "light":"#e3f1e4", "dark":"#5d9274", "last":"#c6dd8b", "selected":"#fff0a0", "accent":"#28734f"},
    "crimson": {"name":"Crimson", "light":"#f2e1dc", "dark":"#a35459", "last":"#e4b25b", "selected":"#ffe08b", "accent":"#762c3c"},
    "desert": {"name":"Desert", "light":"#f3e5c5", "dark":"#b98750", "last":"#dfb965", "selected":"#fff0a6", "accent":"#80532d"},
    "rose": {"name":"Rose", "light":"#f4e4ed", "dark":"#ad6c91", "last":"#e8c26e", "selected":"#fff0a3", "accent":"#813c69"},
    "monochrome": {"name":"Monochrome", "light":"#f0f0f0", "dark":"#808080", "last":"#c9c9c9", "selected":"#f2dc54", "accent":"#414141"},
}
PIECE_STYLES = {
    "tournament": {"name":"Tournament", "glyph":"unicode", "font":"symbol", "scale":.70, "weight":"normal", "shadow":True},
    "modern": {"name":"Modern", "glyph":"unicode", "font":"symbol", "scale":.68, "weight":"bold", "shadow":False},
    "badge": {"name":"Badge", "glyph":"unicode", "font":"symbol", "scale":.57, "weight":"normal", "shadow":False, "badge":True},
    "bold": {"name":"Bold", "glyph":"unicode", "font":"symbol", "scale":.76, "weight":"bold", "shadow":True},
}


def fit_dialog_to_screen(dialog, parent, preferred_width: int, preferred_height: int) -> None:
    """Keep large study dialogs roomy without letting them spill off smaller displays."""
    screen=parent.screen() if parent is not None else QApplication.primaryScreen()
    if screen is None:return
    area=screen.availableGeometry()
    dialog.resize(min(preferred_width,max(480,int(area.width()*.94))),
                  min(preferred_height,max(420,int(area.height()*.92))))


def eval_cp(score: chess.engine.PovScore) -> int:
    score = score.white()
    if score.is_mate():
        m = score.mate()
        return 100_000 if m is None or m > 0 else -100_000
    return int(score.score(mate_score=100_000) or 0)


def engine_line_forces_mate(score: chess.engine.PovScore, color: chess.Color) -> bool:
    """True only when the score says this side can force mate, not receive it."""
    distance=score.pov(color).mate()
    return distance is not None and distance>0


def eval_text(cp: int) -> str:
    if abs(cp) >= 99_000:
        return "+M" if cp > 0 else "−M"
    return f"{cp / 100:+.2f}"


def evaluation_label(cp: int, mate: Optional[int] = None) -> str:
    if mate is None:return eval_text(cp)
    if mate>0:return f"+M{mate}"
    if mate<0:return f"−M{abs(mate)}"
    return "M0"


def expected_points(cp_white: int) -> float:
    """Smoothly map a White-POV centipawn score to an expected game score."""
    bounded=max(-4000,min(4000,int(cp_white)))
    return 1.0/(1.0+math.exp(-bounded/500.0))


MOVE_QUALITY_TOOLTIP=("Local accuracy is a CAPS2-inspired grade based on Stockfish's expected-score loss. "
                      "The nonlinear grade scale gives small errors a visible cost, so it is not a literal "
                      "percentage of expected score retained and is not Chess.com's exact CAPS2 formula.")
REVIEW_IMPACT_TOOLTIP=("Impact compares the played move with Stockfish's top line. "
                       "Expected-score loss is shown in percentage points (pp); the centipawn gap is separate.")


def raw_move_accuracy(loss: int = 0, ep_loss: Optional[float] = None) -> float:
    """Uncalibrated expected-score retention used as input to the local accuracy grade."""
    if ep_loss is not None:
        return max(0.0,min(100.0,100.0*(1.0-max(0.0,min(1.0,ep_loss)))))
    return max(0.0,min(100.0,100.0*math.exp(-.0035*min(1500,max(0,loss)))))


def calibrate_accuracy(raw_score: float) -> float:
    """Compress the high end of expected-score retention into a more useful grade scale."""
    bounded=max(0.0,min(100.0,float(raw_score)))
    return 100.0*(bounded/100.0)**ACCURACY_CALIBRATION_EXPONENT


def uncalibrate_accuracy(grade: float) -> float:
    bounded=max(0.0,min(100.0,float(grade)))
    return 100.0*(bounded/100.0)**(1.0/ACCURACY_CALIBRATION_EXPONENT)


def move_accuracy(loss: int = 0, ep_loss: Optional[float] = None) -> float:
    """Per-move CAPS2-inspired quality grade, with mate-safe fallback."""
    return calibrate_accuracy(raw_move_accuracy(loss,ep_loss))


def review_accuracy(moves) -> Optional[float]:
    """Calibrate a game's mean raw move retention once, avoiding top-heavy raw averages."""
    if not moves:return None
    raw_values=[]
    for move in moves:
        grade=getattr(move,"accuracy",None)
        raw_values.append(uncalibrate_accuracy(grade) if grade is not None else
                          raw_move_accuracy(getattr(move,"loss",0),getattr(move,"expected_loss",None)))
    return calibrate_accuracy(sum(raw_values)/len(raw_values))


def format_review_impact(expected_loss: float, cp_loss: int) -> str:
    return f"{expected_loss*100:.1f} pp expected score lost · {cp_loss} cp"


def count_label(count: int, singular: str, plural: Optional[str] = None) -> str:
    """Format a counted noun without exposing template-style '(s)' copy."""
    count=int(count)
    noun=singular if abs(count)==1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def normalized_move_loss(cp_before: int, cp_after: int, mover: chess.Color,
                        mate_before: Optional[int] = None, mate_after: Optional[int] = None) -> tuple[int,float]:
    """Return centipawn and expected-points loss from the mover's perspective."""
    sign=1 if mover==chess.WHITE else -1
    cp_loss=max(0,(int(cp_before)-int(cp_after))*sign)
    before_white=expected_points(cp_before);after_white=expected_points(cp_after)
    before_mover=before_white if mover==chess.WHITE else 1-before_white
    after_mover=after_white if mover==chess.WHITE else 1-after_white
    ep_loss=max(0.0,before_mover-after_mover)
    if mate_before is not None and mate_after is not None:
        sign=1 if mover==chess.WHITE else -1
        before_mate=mate_before*sign;after_mate=mate_after*sign
        delayed=0
        if before_mate>0 and after_mate>before_mate:delayed=after_mate-before_mate
        elif before_mate<0 and after_mate<0 and abs(after_mate)<abs(before_mate):delayed=abs(before_mate)-abs(after_mate)
        if delayed:
            cp_loss=max(cp_loss,delayed*120)
            ep_loss=max(ep_loss,min(.35,delayed*.025))
    return cp_loss,ep_loss


def override_review_category(verdict: str, color: str, legal_count: int, book_move: bool) -> tuple[str,str]:
    if legal_count<=1:return "Forced","#8aa6c1"
    if book_move:return "Book","#64b5d6"
    return verdict,color


def classify(loss: int, ep_loss: Optional[float] = None) -> tuple[str, str]:
    """Classify by expected-score change; centipawn thresholds are legacy fallback."""
    if ep_loss is None:
        tag="Best" if loss<10 else "Excellent" if loss<30 else "Good" if loss<70 else "Inaccuracy" if loss<130 else "Mistake" if loss<250 else "Blunder"
    else:
        tag="Best" if ep_loss<.005 else "Excellent" if ep_loss<.02 else "Good" if ep_loss<.05 else "Inaccuracy" if ep_loss<.10 else "Mistake" if ep_loss<.20 else "Blunder"
    colors={"Best":"#39a982","Excellent":"#4baf73","Good":"#8bbf65","Inaccuracy":"#e0b746","Mistake":"#e68a47","Blunder":"#e05252","Great":"#45b9d0","Brilliant":"#b68ae0","Miss":"#e68a47","Book":"#64b5d6","Forced":"#8aa6c1"}
    return tag,colors[tag]


def review_verdict_tooltip(item: ReviewMove) -> str:
    """Explain the visible move label and the confidence behind it."""
    definitions={
        "Brilliant":"A sound piece sacrifice that deep analysis confirms as the unique best choice.",
        "Great":"A verified turnaround or a move that stands far above the other choices.",
        "Best":"Stockfish's top choice in this position.",
        "Excellent":"Very close to the top choice; expected score loss is below 2 percentage points.",
        "Good":"A reasonable move; expected score loss is below 5 percentage points.",
        "Inaccuracy":"A small but meaningful drop in expected score (5–10 percentage points).",
        "Mistake":"A significant drop in expected score (10–20 percentage points).",
        "Blunder":"A major drop in expected score (20 percentage points or more).",
        "Miss":"A clear winning chance or forced mate was available and went unclaimed.",
        "Book":"This move appears in the loaded opening book.",
        "Forced":"This was the only legal move.",
    }
    description=definitions.get(item.verdict,"Engine classification based on the analyzed position.")
    impact=f"Expected score lost vs Stockfish's top line: {item.expected_loss*100:.1f} percentage points (pp)."
    return f"{description}\n{impact}\nAnalysis: {item.confidence}."


def near_best_moves(board: chess.Board, lines: list[dict], mover: chess.Color,
                   tolerance: float = NEAR_BEST_EXPECTED_POINTS_TOLERANCE) -> tuple[chess.Move, ...]:
    """Return tied MultiPV choices, respecting forced-mate outcomes and distance."""
    scored=[]
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        mover_ep=white_ep if mover==chess.WHITE else 1-white_ep
        scored.append((pv[0],mover_ep,score.pov(mover).mate()))
    if not scored:return ()
    winning_mates=[mate for _move,_value,mate in scored if mate is not None and mate>0]
    if winning_mates:
        shortest=min(winning_mates)
        return tuple(dict.fromkeys(move for move,_value,mate in scored if mate==shortest))
    non_mates=[value for _move,value,mate in scored if mate is None]
    if non_mates:
        best_ep=max(non_mates)
        return tuple(dict.fromkeys(move for move,value,mate in scored
                                   if mate is None and best_ep-value<=tolerance))
    losing_mates=[abs(mate) for _move,_value,mate in scored if mate is not None and mate<0]
    if losing_mates:
        slowest=max(losing_mates)
        return tuple(dict.fromkeys(move for move,_value,mate in scored if mate==-slowest))
    best_ep=max(value for _move,value,_mate in scored)
    return tuple(dict.fromkeys(move for move,value,_mate in scored if best_ep-value<=tolerance))


@dataclass
class Opening:
    eco: str
    name: str
    sans: tuple[str, ...]
    position_keys: tuple[str, ...]


def position_key(board: chess.Board) -> str:
    # FEN core omits move clocks, retaining all rules that affect legal moves.
    fields = board.fen().split()
    return " ".join(fields[:4])


def parse_pgn_line(text: str) -> list[chess.Move]:
    """Parse Lichess's PGN-formatted third TSV column into legal moves."""
    game = chess.pgn.read_game(io.StringIO(text.strip()))
    if game is not None and game.errors:
        # Tolerate lightweight move-line strings, but don't silently accept corrupt PGN.
        raise ValueError(str(game.errors[0]))
    return list(game.mainline_moves()) if game is not None else []


def opening_from_file(path: Path) -> list[Opening]:
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                continue
            eco, name, pgn = (cell.strip() for cell in row[:3])
            if eco.lower() == "eco" and name.lower() == "name":
                continue
            if not eco or not name or not pgn:
                continue
            try:
                moves = parse_pgn_line(pgn)
                board = chess.Board()
                sans, keys = [], []
                for move in moves:
                    if move not in board.legal_moves:
                        raise ValueError("illegal move in opening line")
                    sans.append(board.san(move))
                    board.push(move)
                    keys.append(position_key(board))
                if sans:
                    result.append(Opening(eco, name, tuple(sans), tuple(keys)))
            except (ValueError, AssertionError):
                continue
    return result


def load_openings(paths: list[Path]) -> list[Opening]:
    all_openings = []
    for path in paths:
        try:
            all_openings.extend(opening_from_file(path))
        except (OSError, UnicodeError, csv.Error):
            continue
    return all_openings


def discover_lichess_files(folder: Path) -> list[Path]:
    return [folder / f"{letter}.tsv" for letter in "abcde" if (folder / f"{letter}.tsv").is_file()]


def detect_opening(moves: list[chess.Move], openings: list[Opening]) -> str:
    if not openings:
        return "Opening database not loaded"
    by_position = {}
    for opening in openings:
        for ply, key in enumerate(opening.position_keys, 1):
            current = by_position.get(key)
            if current is None or ply > current[0]:
                by_position[key] = (ply, opening)
    board = chess.Board()
    match = None
    for ply, move in enumerate(moves, 1):
        if move not in board.legal_moves:
            break
        board.push(move)
        item = by_position.get(position_key(board))
        if item and (match is None or ply >= match[0]):
            match = (ply, item[1])
    if match:
        op = match[1]
        return f"{op.name} · {op.eco}"
    return "Opening not found in local database"


def find_opening_ply(moves: list[chess.Move], openings: list[Opening]) -> int:
    known_positions={key for opening in openings for key in opening.position_keys}
    if not known_positions:return 0
    board=chess.Board();last=0
    for ply,move in enumerate(moves,1):
        if move not in board.legal_moves:break
        board.push(move)
        if position_key(board) in known_positions:last=ply
    return last


def local_opening_exit_depth(pgn_path, user_color, book_positions):
    """Count a player's book moves when their move is the first local-book exit."""
    if not book_positions:return None
    try:
        with Path(pgn_path).open("r",encoding="utf-8-sig") as source:
            game=chess.pgn.read_game(source)
        if game is None or game.board().chess960:return None
        board=game.board();user_book_moves=0
        for move in game.mainline_moves():
            if move not in board.legal_moves:break
            mover="White" if board.turn==chess.WHITE else "Black"
            was_book=position_key(board) in book_positions
            board.push(move);remains_book=position_key(board) in book_positions
            if remains_book:
                if mover==user_color:user_book_moves+=1
                continue
            if was_book and mover==user_color:return user_book_moves
            if was_book:return None
        return None
    except (OSError,UnicodeError,ValueError):
        return None


@dataclass(frozen=True)
class ReviewEvent:
    kind: str
    text: str
    severity: str = "info"
    prompt: str = ""
    focus_squares: tuple[chess.Square,...] = ()


@dataclass
class ReviewMove:
    ply: int
    san: str
    best_san: str
    verdict: str
    color: str
    loss: int
    eval_after: int
    pv: str
    explanation: str
    side: str
    book_status: str
    book_weight: int
    phase: str
    accuracy: float
    best_move: chess.Move
    expected_loss: float = 0.0
    confidence: str = "Standard analysis"
    theme: str = ""
    eval_before: int = 0
    opponent_response: str = ""
    opponent_pv: str = ""
    best_pv_moves: tuple[chess.Move, ...] = ()
    events: tuple[ReviewEvent, ...] = ()
    best_reason: str = ""
    mate_before: Optional[int] = None
    mate_after: Optional[int] = None
    opponent_move: Optional[chess.Move] = None
    acceptable_moves: tuple[chess.Move, ...] = ()
    alternative_sans: tuple[str, ...] = ()
    move_number: int = 0
    opponent_pv_moves: tuple[chess.Move, ...] = ()


TACTICAL_REVIEW_EVENT_KINDS=frozenset({"fork","skewer","discovered_attack","discovered_check",
                                      "removed_defender","new_pin","opponent_hanging_piece","promotion","sound_sacrifice",
                                      "pv_material_gain"})
TACTICAL_REVIEW_VERDICTS=frozenset({"Best","Excellent","Good","Great","Brilliant"})


def has_review_tactical_idea(item: ReviewMove) -> bool:
    return item.verdict in TACTICAL_REVIEW_VERDICTS and any(
        event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive"
        for event in getattr(item,"events",()))


def normalized_syzygy_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path))) if path else ""


def serialize_review_moves(items: list[ReviewMove], syzygy_path: str = "") -> str:
    """Encode an engine review for the optional local full-game archive."""
    payload=[]
    for item in items:
        row=dict(item.__dict__)
        for key in ("best_move","opponent_move"):
            row[key]=row[key].uci() if row[key] else None
        for key in ("best_pv_moves","acceptable_moves","opponent_pv_moves"):
            row[key]=[move.uci() for move in row[key]]
        row["events"]=[event.__dict__ | {"focus_squares":list(event.focus_squares)} for event in row["events"]]
        payload.append(row)
    return json.dumps({"version":REVIEW_ANALYSIS_VERSION,"syzygy_path":normalized_syzygy_path(syzygy_path),
                       "moves":payload},separators=(",",":"))


def deserialize_review_moves(value: str, syzygy_path: str = "") -> list[ReviewMove]:
    """Decode a cached review, rejecting malformed or incompatible payloads."""
    saved=json.loads(value)
    if not isinstance(saved,dict) or saved.get("version")!=REVIEW_ANALYSIS_VERSION:
        raise ValueError("Saved review was produced by older analysis rules")
    if saved.get("syzygy_path","")!=normalized_syzygy_path(syzygy_path):
        raise ValueError("Saved review used different Syzygy tablebases")
    rows=saved.get("moves")
    if not isinstance(rows,list):raise ValueError("Saved review is not a list")
    items=[]
    for row in rows:
        row=dict(row)
        for key in ("best_move","opponent_move"):
            row[key]=chess.Move.from_uci(row[key]) if row.get(key) else None
        for key in ("best_pv_moves","acceptable_moves","opponent_pv_moves"):
            row[key]=tuple(chess.Move.from_uci(move) for move in row.get(key,[]))
        row["alternative_sans"]=tuple(row.get("alternative_sans",()))
        row["events"]=tuple(ReviewEvent(**(event | {"focus_squares":tuple(event.get("focus_squares",()))})) for event in row.get("events",[]))
        items.append(ReviewMove(**row))
    return items


def review_move_notation(item: ReviewMove) -> str:
    """Format a reviewed ply as standard chess move notation, including Black's ellipsis."""
    number=getattr(item,"move_number",0) or (item.ply+1)//2
    marker="." if item.side=="White" else "..."
    return f"{number}{marker} {item.san}"


def review_text_for_viewer(text: str, item: ReviewMove,
                           user_color: Optional[chess.Color]) -> str:
    """Name the mover explicitly when coaching text describes the opponent's move."""
    if not text or user_color is None:
        return text
    mover_color=chess.WHITE if item.side=="White" else chess.BLACK
    if mover_color==user_color:return text
    side=item.side
    replacements=(("worse for you",f"worse for {side}"),("better for you",f"better for {side}"),
                  ("in favor of you",f"in favor of {side}"),("ahead of you",f"ahead of {side}"),
                  ("to you",f"to {side}"),("for you",f"for {side}"),
                  ("against you",f"against {side}"),("from you",f"from {side}"),
                  ("with you",f"with {side}"),("You keep",f"{side} keeps"),("You preserve",f"{side} preserves"),
                  ("You give",f"{side} gives"),("You have",f"{side} has"),
                  ("You are",f"{side} is"),("You were",f"{side} was"),
                  ("You find",f"{side} finds"),("You missed",f"{side} missed"),
                  ("You found",f"{side} found"),("you keep",f"{side} keeps"),
                  ("you preserve",f"{side} preserves"),("you give",f"{side} gives"),
                  ("you have",f"{side} has"),("you are",f"{side} is"),
                  ("you were",f"{side} was"),("you find",f"{side} finds"),
                  ("you missed",f"{side} missed"),("you found",f"{side} found"),
                  ("Your",f"{side}'s"),("your",f"{side}'s"),("You",side),("you",side))
    for source,target in replacements:
        text=re.sub(rf"\b{re.escape(source)}\b",target,text)
    return text


def parse_review_san_line(board: chess.Board, san_line: str) -> tuple[chess.Move, ...]:
    """Rebuild a saved reply line from SAN when loading reviews archived before UCI lines were stored."""
    position=board.copy(stack=False);moves=[]
    for san in san_line.split():
        try:move=position.parse_san(san)
        except ValueError:break
        moves.append(move);position.push(move)
    return tuple(moves)


def review_spoken_text(item: ReviewMove, user_color: Optional[chess.Color] = None) -> str:
    """Build a concise spoken version of the selected move's coaching detail."""
    explanation=review_text_for_viewer(item.explanation,item,user_color)
    parts=[f"{review_move_notation(item)}, {item.verdict.lower()}.",explanation]
    if user_color is not None:
        is_user_move=(item.side=="White")== (user_color==chess.WHITE)
        owner="your" if is_user_move else "your opponent's"
        parts.insert(1,f"That was {owner} move as {item.side}.")
    if item.best_san!=item.san and item.best_reason:
        parts.append("Engine comparison: "+review_text_for_viewer(item.best_reason,item,user_color))
    if item.alternative_sans:
        parts.append("Other near-equal choices: "+", ".join(item.alternative_sans)+".")
    if item.opponent_response:
        parts.append("Strongest reply: "+item.opponent_response+".")
    prompt=review_text_for_viewer(review_reflection_prompt(item.events) or "",item,user_color)
    if prompt:parts.append("Think it through: "+prompt)
    return " ".join(part.strip() for part in parts if part and part.strip())


def review_game_narrative(items: list[ReviewMove],
                         user_color: Optional[chess.Color] = None) -> str:
    """Lead with the defining moment, then add a separate major swing if useful."""
    if not items:return "No reviewed moves are available yet."
    final=items[-1]
    largest=max(items,key=lambda item:item.expected_loss)
    voice_seed=sum(ord(char) for item in items for char in item.san)+sum(item.ply for item in items)

    def swing_story(item):
        notation=review_move_notation(item);alternative=getattr(item,"best_san","")
        top_choice=f"Stockfish's top choice, {alternative}" if alternative else "the engine's preferred continuation"
        points=item.expected_loss*100
        unit="percentage point" if round(points,1)==1.0 else "percentage points"
        if item.verdict=="Miss":
            templates=(
                f"The clearest missed chance was {notation} by {item.side}: it cost {points:.1f} {unit} of expected score compared with {top_choice}.",
                f"On {notation}, {item.side} missed a chance. Stockfish estimates {alternative or 'the top line'} kept {points:.1f} {unit} more expected score.",
                f"Stockfish found a stronger opportunity at {notation}: {item.side}'s move gave up {points:.1f} {unit} against {top_choice}.",
                f"The missed opportunity came on {notation}. The engine rates {top_choice} {points:.1f} {unit} higher in expected score than the played move.")
        else:
            templates=(
                f"The biggest swing came on {notation} by {item.side}: the played move cost {points:.1f} {unit} of expected score compared with {top_choice}.",
                f"On {notation}, {item.side}'s move left {points:.1f} {unit} of expected score on the table; Stockfish preferred {alternative or 'another continuation'}.",
                f"Stockfish's largest disagreement with the game was {notation}: it rates {alternative or 'its top line'} {points:.1f} {unit} ahead of the move played.",
                f"The position shifted most after {notation}. The engine's top line held {points:.1f} {unit} more expected score than {item.side}'s move.")
        return templates[voice_seed%len(templates)]

    story="";story_ply=None
    if any(event.kind=="checkmate" and event.severity=="positive"
           for event in getattr(final,"events",())):
        story=f"The game ended in checkmate on {review_move_notation(final)} by {final.side}."
        story_ply=final.ply
    elif any(event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"}
             for event in getattr(final,"events",())):
        ending=next(event for event in final.events
                    if event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"})
        story=f"The game ended after {review_move_notation(final)} by {final.side}. {ending.text}"
        story_ply=final.ply
    standout=next((item for item in items if item.verdict=="Brilliant"
                   and any(event.kind=="sound_sacrifice" and event.severity=="positive" for event in item.events)),None)
    if not story and standout:
        event=next(event for event in standout.events if event.kind=="sound_sacrifice" and event.severity=="positive")
        event_text=review_text_for_viewer(event.text,standout,user_color)
        story=f"Standout: {review_move_notation(standout)} by {standout.side} was a confirmed sacrifice. {event_text}"
        story_ply=standout.ply
    great=max((item for item in items if item.verdict=="Great"),
              key=lambda item:item.expected_loss,default=None)
    if great and (not story or story_ply==final.ply):
        only_move=next((event for event in great.events if event.kind=="only_good_move"),None)
        turnaround=next((event for event in great.events if event.kind=="turnaround"),None)
        if turnaround:
            highlight=f"Turning point: {review_move_notation(great)} by {great.side}. {turnaround.text}"
        elif only_move:
            highlight=f"Standout resource: {review_move_notation(great)} was the only move that held at least an equal position. {only_move.text}"
        else:
            highlight=(f"Turning point: {review_move_notation(great)} brought {great.side} back from a difficult position; "
                       "the engine's expected-score estimate improved from below 25% to at least 50%.")
        story=(story+" "+highlight).strip()
        story_ply=great.ply
    tactical_items=[item for item in items if has_review_tactical_idea(item)]
    tactic=max(tactical_items,key=lambda item:(sum(event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive"
                                                    for event in item.events),
                                               item.expected_loss,-item.ply),default=None)
    if not story and tactic:
        event=next(event for event in tactic.events if event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive")
        event_text=review_text_for_viewer(event.text,tactic,user_color)
        story=f"Tactical highlight: {review_move_notation(tactic)} by {tactic.side}. {event_text}"
        story_ply=tactic.ply
    if not story and largest.expected_loss>=.05:
        return swing_story(largest)
    if not story:
        steady_stories=(f"No single move changed much: the largest gap from Stockfish's top choice was "
                        f"{largest.expected_loss*100:.1f} percentage points, on {review_move_notation(largest)}.",
                        f"The move-by-move choices stayed close to Stockfish's top lines; the largest gap was "
                        f"{largest.expected_loss*100:.1f} percentage points on {review_move_notation(largest)}.",
                        f"The closest call was {review_move_notation(largest)} by {largest.side}: it was only "
                        f"{largest.expected_loss*100:.1f} percentage points from the top choice.")
        story=steady_stories[voice_seed%len(steady_stories)]
        story_ply=largest.ply
    last_book_item=None;first_book_exit=None
    for item in items:
        book_status=getattr(item,"book_status","")
        if book_status.startswith("Book move"):
            last_book_item=item
        elif last_book_item and book_status=="Out of book":
            first_book_exit=item;break
    if first_book_exit:
        opening_note=(f"Opening checkpoint: {review_move_notation(first_book_exit)} by {first_book_exit.side} "
                      f"was the first move outside the loaded book after {review_move_notation(last_book_item)}. "
                      f"Stockfish rated it {first_book_exit.verdict}.")
        impact=review_moment_impact(first_book_exit)
        if impact:opening_note+=f" It gave up {impact} against the top line."
        story+=" "+opening_note
    if largest.expected_loss>=.05 and largest.ply!=story_ply:
        story+=" "+swing_story(largest)
    return story


def game_phase(board: chess.Board) -> str:
    non_pawn_material=sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.piece_type!=chess.PAWN and p.piece_type!=chess.KING)
    return "Opening" if board.fullmove_number<=12 else "Endgame" if non_pawn_material<=13 else "Middlegame"


def castling_timing_label(phase,ply):
    if phase=="Opening":
        try:move_number=max(1,(int(ply)+1)//2)
        except (TypeError,ValueError):return "Opening · move unknown"
        if move_number<3:return "Opening · moves 1–2"
        if move_number<=6:return f"Opening · move {move_number}"
        return "Opening · move 7+"
    return phase if phase in ("Middlegame","Endgame") else "Did not castle"


def describe_move(before: chess.Board, move: chess.Move, after: chess.Board,
                  loss: int, best_san: str, material_delta: int, verdict: str = "Good",
                  theme: str = "") -> str:
    who = "White" if before.turn else "Black"
    facts = []
    if before.is_capture(move):
        victim = before.piece_at(move.to_square)
        if before.is_en_passant(move): facts.append("captures en passant")
        elif victim: facts.append(f"wins a {chess.piece_name(victim.piece_type)}")
    if move.promotion: facts.append(f"promotes to a {chess.piece_name(move.promotion)}")
    if before.is_castling(move): facts.append("castles to improve king safety")
    if after.is_checkmate(): facts.append("delivers checkmate")
    elif after.is_check(): facts.append("gives check")
    if material_delta:
        side_gain = material_delta if before.turn == chess.WHITE else -material_delta
        if side_gain > 0: facts.append(f"comes out about {count_label(side_gain,'material point')} ahead")
        elif side_gain < 0: facts.append(f"ends up about {count_label(abs(side_gain),'material point')} down")
    if verdict == "Miss": facts.append(f"misses a chance to play {best_san}, which would have created a clearly better position")
    elif verdict == "Blunder": facts.append(f"{best_san} was the best defence; this move gives up about {loss/100:.1f} pawns of engine evaluation")
    elif verdict == "Mistake": facts.append(f"{best_san} was stronger; the move gives up about {loss/100:.1f} pawns of evaluation")
    elif verdict == "Inaccuracy": facts.append(f"{best_san} keeps more of the position's value (about {loss/100:.1f} pawns)")
    elif verdict in ("Best","Brilliant"):
        facts.append("matches or closely matches the engine's top choice")
    elif verdict=="Great":
        facts.append("finds a resourceful way to improve a difficult position")
    if theme: facts.append(theme)
    if not facts: facts.append("keeps the position close to its previous evaluation")
    return who + " " + "; ".join(facts) + "."


def material_balance(board: chess.Board) -> int:
    score = 0
    for piece_type, value in VALUES.items():
        score += value * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
    return score


def captured_piece_details(board: chess.Board, move: chess.Move) -> tuple[Optional[chess.Piece],Optional[chess.Square]]:
    """Return the captured piece and its square, including en-passant captures."""
    if move not in board.legal_moves or not board.is_capture(move):return None,None
    square=move.to_square;victim=board.piece_at(square)
    if board.is_en_passant(move):
        square=move.to_square-8 if board.turn==chess.WHITE else move.to_square+8
        victim=chess.Piece(chess.PAWN,not board.turn)
    return victim,square


def legal_capture_map(board: chess.Board, attacker_color: chess.Color) -> dict[chess.Square,set[chess.Square]]:
    """Map occupied targets to pieces that can legally capture them."""
    probe=board.copy(stack=False);probe.turn=attacker_color
    captures={}
    for move in probe.legal_moves:
        if probe.is_capture(move) and probe.piece_at(move.to_square) is not None:
            captures.setdefault(move.to_square,set()).add(move.from_square)
    return captures


def legal_defender_map(board: chess.Board, defender_color: chess.Color, include_pawns=False,
                       include_king=False) -> dict[chess.Square,set[chess.Square]]:
    """Map friendly pieces to legal captures they could make on their square."""
    defenders={}
    for square,piece in board.piece_map().items():
        if (piece.color!=defender_color or (piece.piece_type==chess.PAWN and not include_pawns)
                or (piece.piece_type==chess.KING and not include_king)):continue
        probe=board.copy(stack=False)
        probe.remove_piece_at(square)
        probe.set_piece_at(square,chess.Piece(chess.PAWN,not defender_color))
        probe.turn=defender_color
        for move in probe.legal_moves:
            if move.to_square==square and probe.is_capture(move):
                defenders.setdefault(square,set()).add(move.from_square)
    return defenders


def skewer_lines(board: chess.Board, attacker_color: chess.Color) -> list[tuple[chess.Piece,chess.Square,chess.Piece,chess.Square,chess.Piece,chess.Square]]:
    """Find slider lines attacking a more valuable front piece and one behind it."""
    lines=[];target_color=not attacker_color
    for source,piece in board.piece_map().items():
        if piece.color!=attacker_color or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        directions=[]
        if piece.piece_type in (chess.BISHOP,chess.QUEEN):directions.extend(((-1,-1),(-1,1),(1,-1),(1,1)))
        if piece.piece_type in (chess.ROOK,chess.QUEEN):directions.extend(((-1,0),(1,0),(0,-1),(0,1)))
        for df,dr in directions:
            file,rank=chess.square_file(source)+df,chess.square_rank(source)+dr;found=[]
            while 0<=file<8 and 0<=rank<8:
                square=chess.square(file,rank);target=board.piece_at(square)
                if target:
                    if target.color==target_color:found.append((target,square))
                    if len(found)>=2 or target.color==attacker_color:break
                file+=df;rank+=dr
            if (len(found)>=2 and VALUES.get(found[0][0].piece_type,0)>VALUES.get(found[1][0].piece_type,0)
                    and found[0][0].piece_type in (chess.QUEEN,chess.ROOK,chess.KING)):
                lines.append((piece,source,found[0][0],found[0][1],found[1][0],found[1][1]))
    return lines


def newly_created_skewers(before: chess.Board, after: chess.Board,
                          attacker_color: chess.Color):
    """Return only skewers that were absent before the move."""
    def key(line):
        pinner,source,front,front_square,rear,rear_square=line
        return (source,pinner.piece_type,front_square,front.piece_type,
                rear_square,rear.piece_type)
    old={key(line) for line in skewer_lines(before,attacker_color)}
    return [line for line in skewer_lines(after,attacker_color) if key(line) not in old]


def fork_targets(board: chess.Board, move: chess.Move) -> list[tuple[chess.Piece,chess.Square]]:
    """Return valuable targets the moved piece can legally attack after the move."""
    if move not in board.legal_moves:return []
    after=board.copy(stack=False);after.push(move);mover=board.turn
    moved_piece=after.piece_at(move.to_square);targets=[]
    if moved_piece:
        probe=after.copy(stack=False);probe.turn=mover
        legal_capture_squares={candidate.to_square for candidate in probe.legal_moves
                               if candidate.from_square==move.to_square}
        checking_squares=set(after.checkers())
        for square in after.attacks(move.to_square):
            piece=after.piece_at(square)
            legal_attack=(move.to_square in checking_squares if piece and piece.piece_type==chess.KING
                          else square in legal_capture_squares)
            if (piece and piece.color!=mover and legal_attack
                    and (piece.piece_type==chess.KING or VALUES.get(piece.piece_type,0)>=3)):
                targets.append((piece,square))
    return targets


def tactical_themes(board: chess.Board, move: chess.Move) -> set[str]:
    """Recognize a few concrete tactical motifs to label locally generated puzzles."""
    if move not in board.legal_moves:return set()
    mover=board.turn;opponent=not mover;after=board.copy();after.push(move);themes=set()
    if after.is_checkmate():themes.add("Mate")
    elif after.is_check():themes.add("Check")
    if move.promotion:themes.add("Promotion")
    victim,capture_square=captured_piece_details(board,move)
    attacker=board.piece_at(move.from_square)
    if victim and victim.color!=mover:
        victim_value=VALUES.get(victim.piece_type,0);attacker_value=VALUES.get(attacker.piece_type,0) if attacker else 0
        defended=bool(board.attackers(not mover,capture_square))
        if victim_value>=5 or victim_value>=attacker_value+2 or not defended:themes.add("Winning material")
    targets=[piece.piece_type for piece,_ in fork_targets(board,move)]
    if len(targets)>=2 and any(target in (chess.KING,chess.QUEEN,chess.ROOK) for target in targets):themes.add("Fork")
    if (any(piece.color==opponent for _pinner,_source,piece,_square in newly_pinned_pieces(board,after))
            or newly_relative_pins(board,after,mover)):
        themes.add("Pin")
    for source,piece in board.piece_map().items():
        if piece.color!=mover or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN) or source==move.from_square:continue
        newly_attacked=set(after.attacks(source))-set(board.attacks(source))
        for square in newly_attacked:
            target=after.piece_at(square)
            if target is not None and target.color!=mover and target.piece_type in (chess.KING,chess.QUEEN,chess.ROOK):
                themes.add("Discovered attack")
                if target.piece_type==chess.KING:themes.add("Discovered check")
                break
        if "Discovered attack" in themes:break
    # Keep puzzle labels and review explanations grounded in the same line test.
    if newly_created_skewers(board,after,mover):themes.add("Skewer")
    return themes


def newly_opened_slider_targets(before: chess.Board, after: chess.Board,
                                moving_square: chess.Square) -> list[tuple[chess.Piece,chess.Square,chess.Piece,chess.Square]]:
    """Return enemy king, queen, or rook squares newly attacked by an unmoved slider."""
    mover=before.turn;opened=[]
    for source,piece in after.piece_map().items():
        if piece.color!=mover or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        if source==moving_square:continue
        new_attacks=set(after.attacks(source))-set(before.attacks(source))
        for target_square in new_attacks:
            target=after.piece_at(target_square)
            if target and target.color!=mover and target.piece_type in (chess.KING,chess.QUEEN,chess.ROOK):
                opened.append((piece,source,target,target_square))
    return sorted(opened,key=lambda item:(-VALUES.get(item[2].piece_type,0),item[1],item[3]))


def newly_pinned_pieces(before: chess.Board, after: chess.Board):
    """Return newly absolutely pinned pieces with the pinning slider and squares."""
    pins=[]
    for square,piece in after.piece_map().items():
        if piece.piece_type==chess.KING:continue
        color=piece.color
        if not after.is_pinned(color,square) or before.is_pinned(color,square):continue
        pin_ray=after.pin(color,square)
        pinners=[sq for sq in after.attackers(not color,square)
                 if sq in pin_ray and after.piece_at(sq) and after.piece_at(sq).piece_type in (chess.BISHOP,chess.ROOK,chess.QUEEN)]
        if pinners:pins.append((after.piece_at(pinners[0]),pinners[0],piece,square))
    return pins


def relative_pin_lines(board: chess.Board, attacker_color: chess.Color):
    """Find line pieces that attack an enemy piece in front of its rook or queen."""
    lines=[]
    orthogonal=((1,0),(-1,0),(0,1),(0,-1))
    diagonal=((1,1),(1,-1),(-1,1),(-1,-1))
    for source,pinner in board.piece_map().items():
        if pinner.color!=attacker_color or pinner.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        directions=(orthogonal if pinner.piece_type==chess.ROOK else diagonal if pinner.piece_type==chess.BISHOP else orthogonal+diagonal)
        source_file,source_rank=chess.square_file(source),chess.square_rank(source)
        for df,dr in directions:
            first=None
            for distance in range(1,8):
                file,rank=source_file+df*distance,source_rank+dr*distance
                if not (0<=file<8 and 0<=rank<8):break
                square=chess.square(file,rank);piece=board.piece_at(square)
                if piece is None:continue
                if first is None:
                    if piece.color==attacker_color:break
                    first=(piece,square)
                    continue
                front,front_square=first
                if (piece.color!=attacker_color and front.piece_type!=chess.KING
                        and piece.piece_type in (chess.QUEEN,chess.ROOK)
                        and VALUES[piece.piece_type]>VALUES[front.piece_type]):
                    lines.append((pinner,source,front,front_square,piece,square))
                break
    return lines


def newly_relative_pins(before: chess.Board, after: chess.Board, attacker_color: chess.Color):
    old={(source,pinner.piece_type,front_square,target_square,target.piece_type)
         for pinner,source,_front,front_square,target,target_square in relative_pin_lines(before,attacker_color)}
    return [line for line in relative_pin_lines(after,attacker_color)
            if (line[1],line[0].piece_type,line[3],line[5],line[4].piece_type) not in old]


def piece_label(piece: Optional[chess.Piece]) -> str:
    return chess.piece_name(piece.piece_type) if piece else "piece"


def _king_pressure(board: chess.Board, color: chess.Color) -> int:
    """Count squares in the king's zone controlled by the enemy."""
    king=board.king(color)
    if king is None:return 0
    file,rank=chess.square_file(king),chess.square_rank(king)
    zone={king}
    for df in (-1,0,1):
        for dr in (-1,0,1):
            f,r=file+df,rank+dr
            if 0<=f<8 and 0<=r<8:zone.add(chess.square(f,r))
    return sum(bool(board.attackers(not color,square)) for square in zone)


def _controlled_center_squares(board: chess.Board, color: chess.Color) -> set[chess.Square]:
    center=(chess.D4,chess.E4,chess.D5,chess.E5)
    return {square for square in center if board.attackers(color,square)}


def _center_control(board: chess.Board, color: chess.Color) -> int:
    return len(_controlled_center_squares(board,color))


def missed_plan_event(before: chess.Board, played_move: chess.Move, played_after: chess.Board,
                      best_move: chess.Move) -> Optional[ReviewEvent]:
    """Explain a quiet engine alternative only when a concrete positional change is measurable."""
    if best_move not in before.legal_moves:return None
    mover=before.turn;best_after=before.copy(stack=False);best_after.push(best_move)
    best_san=before.san(best_move);played_piece=before.piece_at(best_move.from_square)

    new_center=_controlled_center_squares(best_after,mover)-_controlled_center_squares(played_after,mover)
    if new_center:
        squares=", ".join(chess.square_name(square) for square in sorted(new_center))
        noun="square" if len(new_center)==1 else "squares"
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} adds attacks on the central {noun} {squares} compared with your move.","warning",
                           f"The alternative {best_san} attacks {squares} in the center. Which of those squares matters most for your next plan?",tuple(new_center))

    pressure_after=_king_pressure(played_after,mover);pressure_best=_king_pressure(best_after,mover)
    if pressure_after-pressure_best>=2:
        king=best_after.king(mover)
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} reduces enemy attacks around your king on {chess.square_name(king)} from {pressure_after} to {pressure_best} nearby squares.","warning",
                           f"The alternative {best_san} lowers the pressure around your king. Which line or escape square becomes safer?",(king,))

    if (before.fullmove_number<=12 and played_piece and played_piece.piece_type in (chess.KNIGHT,chess.BISHOP)
            and chess.square_rank(best_move.from_square) in (0,7)
            and chess.square_rank(best_move.to_square) not in (0,7)):
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} develops your {piece_label(played_piece)} from {chess.square_name(best_move.from_square)} to {chess.square_name(best_move.to_square)} instead of leaving it on its starting square.","warning",
                           f"The alternative {best_san} develops your {piece_label(played_piece)}. What useful squares does it reach from {chess.square_name(best_move.to_square)}?",(best_move.from_square,best_move.to_square))

    if played_piece and played_move.from_square==best_move.from_square:
        actual_piece=played_after.piece_at(played_move.to_square)
        best_count=_piece_mobility(best_after,mover,best_move.to_square)
        actual_count=_piece_mobility(played_after,mover,played_move.to_square)
        if actual_piece and actual_piece.piece_type==played_piece.piece_type and best_count>=actual_count+4:
            return ReviewEvent("missed_plan",f"The engine's alternative {best_san} gives your {piece_label(played_piece)} {best_count} legal moves, compared with {actual_count} after your move.","warning",
                               f"The alternative {best_san} gives your {piece_label(played_piece)} more routes. Which route improves its role in the position?",(played_move.to_square,best_move.to_square))
    return None


def removed_defender_event(before: chess.Board, best_move: chess.Move,
                           best_pv: tuple[chess.Move,...], missed: bool = True) -> Optional[ReviewEvent]:
    """Describe a defender capture only when the engine line later wins its target."""
    if best_move not in before.legal_moves or not before.is_capture(best_move):return None
    defender,defender_square=captured_piece_details(before,best_move)
    if defender is None or defender.piece_type==chess.KING:return None
    mover=before.turn;opponent=not mover
    after_best=before.copy(stack=False);after_best.push(best_move)
    for target_square,target in before.piece_map().items():
        if target.color!=opponent or target.piece_type not in (chess.QUEEN,chess.ROOK):continue
        old_defenders=set(before.attackers(opponent,target_square))
        new_defenders=set(after_best.attackers(opponent,target_square))
        removed=old_defenders-new_defenders
        if (best_move.to_square not in removed or not old_defenders
                or len(new_defenders)>=len(old_defenders)
                or not after_best.attackers(mover,target_square)):
            continue
        line=before.copy(stack=False);tracked_square=target_square;line_sans=[];won_target=False
        sequence=best_pv if best_pv and best_pv[0]==best_move else (best_move,)+best_pv
        for line_move in sequence:
            if line_move not in line.legal_moves:break
            line_sans.append(line.san(line_move))
            victim,capture_square=captured_piece_details(line,line_move)
            if line.turn==mover and victim==target and capture_square==tracked_square:
                won_target=True;break
            if line_move.from_square==tracked_square:tracked_square=line_move.to_square
            line.push(line_move)
        if not won_target:continue
        defender_word="the only defender" if not new_defenders else "a defender"
        san_line=" ".join(line_sans)
        prompt=(f"Which capture removes {defender_word} of the {piece_label(target)} on "
                f"{chess.square_name(target_square)}, and how does the engine line win it?")
        lead=f"The best move {before.san(best_move)}" if missed else f"Your move {before.san(best_move)}"
        return ReviewEvent("missed_tactic" if missed else "removed_defender",
                           f"{lead} removes {defender_word} "
                           f"{piece_label(defender)} on {chess.square_name(defender_square)}, which protects "
                           f"the {piece_label(target)} on {chess.square_name(target_square)}. "
                           f"The engine line {san_line} wins the {piece_label(target)}.",
                           "warning" if missed else "positive",prompt,
                           (defender_square,target_square,best_move.from_square))
    return None


def _piece_mobility(board: chess.Board, color: chess.Color, square: chess.Square) -> int:
    probe=board.copy(stack=False);probe.turn=color
    return sum(1 for move in probe.legal_moves if move.from_square==square)


def _pawn_structure(board: chess.Board, color: chess.Color) -> tuple[set[int],set[int]]:
    files={f:[] for f in range(8)}
    for square in board.pieces(chess.PAWN,color):files[chess.square_file(square)].append(square)
    doubled={f for f,squares in files.items() if len(squares)>1}
    occupied={f for f,squares in files.items() if squares}
    isolated={f for f in occupied if f-1 not in occupied and f+1 not in occupied}
    return doubled,isolated


def _safe_piece_escapes(board: chess.Board, color: chess.Color, square: chess.Square) -> int:
    """Conservatively count legal exits to squares not attacked by the opponent."""
    probe=board.copy(stack=False);probe.turn=color
    safe=0
    for move in list(probe.legal_moves):
        if move.from_square!=square:continue
        after=probe.copy(stack=False);after.push(move)
        if not after.attackers(not color,move.to_square):safe+=1
    return safe


def _pv_material_swing(board: chess.Board, pv: tuple[chess.Move,...], color: chess.Color) -> int:
    initial=material_balance(board);line=board.copy(stack=False)
    for move in pv:
        if move not in line.legal_moves:break
        line.push(move)
    change=material_balance(line)-initial
    return change if color==chess.WHITE else -change


def _exchange_gain_on_square(board: chess.Board, square: chess.Square) -> int:
    """Estimate best material gain in a legal capture sequence on one square."""
    best=0
    for move in list(board.legal_moves):
        if move.to_square!=square or not board.is_capture(move):continue
        victim,_=captured_piece_details(board,move)
        if not victim:continue
        gain=VALUES.get(victim.piece_type,0)
        after=board.copy(stack=False);after.push(move)
        gain-=_exchange_gain_on_square(after,square)
        best=max(best,gain)
    return best


def _pv_captures_moved_piece(board: chess.Board, move: chess.Move,
                            pv: tuple[chess.Move,...]) -> bool:
    """Check whether the line actually captures the piece whose move is under review."""
    sequence=pv if pv and pv[0]==move else (move,)+pv
    line=board.copy(stack=False)
    if not sequence or sequence[0] not in line.legal_moves:return False
    tracked_square=sequence[0].to_square
    line.push(sequence[0])
    for reply in sequence[1:]:
        if reply not in line.legal_moves:break
        _victim,capture_square=captured_piece_details(line,reply)
        if capture_square==tracked_square:return True
        if reply.from_square==tracked_square:tracked_square=reply.to_square
        line.push(reply)
    return False


def is_engine_justified_sacrifice(before: chess.Board, move: chess.Move, best_move: chess.Move,
                                 pv: tuple[chess.Move,...], before_mover_ep: float,
                                 actual_mover_ep: float, forced_mate: bool = False) -> bool:
    if move not in before.legal_moves or move!=best_move:return False
    attacker=before.piece_at(move.from_square);victim,_=captured_piece_details(before,move)
    attacker_value=VALUES.get(attacker.piece_type,0) if attacker else 0
    victim_value=VALUES.get(victim.piece_type,0) if victim else 0
    is_sacrifice=attacker_value-victim_value>=2 and attacker_value>=3
    if (not is_sacrifice or before_mover_ep>=BRILLIANT_MAX_PREMOVE_EXPECTED_POINTS
            or actual_mover_ep<max(BRILLIANT_MIN_POSTMOVE_EXPECTED_POINTS,
                                   before_mover_ep-BRILLIANT_MAX_EXPECTED_POINTS_LOSS)):
        return False
    sequence=pv if pv and pv[0]==move else (move,)+pv
    if (_pv_captures_moved_piece(before,move,sequence)
            and _pv_material_swing(before,sequence,before.turn)<=-2):
        return True
    # If the engine declines the offer, confirm that accepting it really wins
    # material after the available recaptures. A merely capturable piece is not
    # enough: a rook trade or a poisoned queen capture is not a sacrifice.
    after=before.copy(stack=False);after.push(move)
    offered=after.piece_at(move.to_square)
    if not offered:return False
    offered_value=VALUES.get(offered.piece_type,0)
    for reply in list(after.legal_moves):
        if not after.is_capture(reply):continue
        captured,capture_square=captured_piece_details(after,reply)
        if captured!=offered or capture_square!=move.to_square:continue
        accepted=after.copy(stack=False);accepted.push(reply)
        if -offered_value+_exchange_gain_on_square(accepted,move.to_square)<=-2:
            return True
    return False


def brilliant_choice_gap(board: chess.Board, lines: list[dict], mover: chess.Color,
                         played_move: chess.Move,
                         minimum_gap: float = BRILLIANT_MIN_REPORTED_GAP) -> float:
    """Return the expected-points gap only when the played move is a clear engine first choice."""
    scored=[]
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        scored.append((pv[0],white_ep if mover==chess.WHITE else 1-white_ep))
    if len(scored)<2:return 0.0
    best_move,best_ep=max(scored,key=lambda item:item[1])
    if best_move!=played_move:return 0.0
    runner_up=max((ep for move,ep in scored if move!=played_move),default=best_ep)
    gap=max(0.0,best_ep-runner_up)
    return gap if gap>=minimum_gap else 0.0


def can_label_brilliant(justified: bool, budget: float, book_move: bool, legal_count: int,
                        unique_best: bool = False) -> bool:
    return bool(justified and unique_best and budget>=.4 and not book_move and legal_count>1)


def only_good_move_gap(board: chess.Board, lines: list[dict], mover: chess.Color,
                       played_move: chess.Move, legal_count: int,
                       before_mover_ep: float) -> float:
    """Return a gap only when every legal move is scored and one preserves equality."""
    if (before_mover_ep>=GREAT_ONLY_MAX_PREMOVE_EXPECTED_POINTS
            or len(lines)<legal_count or legal_count<=1):return 0.0
    scores={}
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        scores[pv[0]]=white_ep if mover==chess.WHITE else 1-white_ep
    if len(scores)!=legal_count:return 0.0
    best_move,best_ep=max(scores.items(),key=lambda item:item[1])
    if best_move!=played_move or best_ep<GREAT_ONLY_MIN_POSTMOVE_EXPECTED_POINTS:return 0.0
    runner_up=max(ep for move,ep in scores.items() if move!=played_move)
    gap=best_ep-runner_up
    return gap if gap>=GREAT_ONLY_MIN_ALTERNATIVE_GAP else 0.0


def analyze_review_events(before: chess.Board, move: chess.Move, after: chess.Board,
                          best_move: chess.Move, response: Optional[chess.Move] = None,
                          best_pv: tuple[chess.Move,...] = (), repeated_piece_moves: int = 0,
                          response_pv: tuple[chess.Move,...] = (),
                          explain_best_difference: bool = True) -> tuple[ReviewEvent,...]:
    """Build concrete, inspectable evidence from the played transition and engine lines."""
    events=[];mover=before.turn;opponent=not mover
    mover_piece=before.piece_at(move.from_square)
    move_san=before.san(move)
    response_san=after.san(response) if response and response in after.legal_moves else ""
    response_takes=response_san and after.is_capture(response)
    response_victim,response_capture_square=(captured_piece_details(after,response)
                                             if response_takes and response else (None,None))
    response_square=(chess.square_name(response_capture_square)
                     if response_capture_square is not None else "")
    if before.is_capture(move):
        victim,capture_square=captured_piece_details(before,move)
        if victim:events.append(ReviewEvent("capture",f"{move_san} captures the opponent's {piece_label(victim)} on {chess.square_name(capture_square)}.","positive",focus_squares=(move.to_square,)))
    if response_takes and response_victim:
        events.append(ReviewEvent("opponent_capture",f"Stockfish's strongest reply is {response_san}, capturing your {piece_label(response_victim)} on {response_square}.","critical",focus_squares=(response_capture_square,)))
    if response_san and after.gives_check(response):
        response_board=after.copy(stack=False);response_board.push(response)
        if response_board.is_checkmate():
            king=response_board.king(mover)
            if king is not None and chess.square_rank(king) in (0,7):
                events.append(ReviewEvent("back_rank_mate",f"Stockfish's strongest reply is {response_san}, delivering back-rank checkmate; your king on {chess.square_name(king)} has no escape.","critical"))
            else:events.append(ReviewEvent("opponent_mate",f"Stockfish's strongest reply is {response_san}, delivering checkmate.","critical"))
        else:events.append(ReviewEvent("opponent_check",f"Stockfish's strongest reply is {response_san}, giving check.","warning"))
    pv_swing=_pv_material_swing(after,response_pv[:8],mover) if response_pv else 0
    pv_loss=max(0,-pv_swing)
    if pv_loss>=2:
        events.append(ReviewEvent("pv_material_loss",f"In the engine's continuation after {response_san or 'the strongest reply'}, the material balance ends about {count_label(pv_loss,'point')} worse for you.","critical"))
    pv_gain=max(0,pv_swing)
    if pv_gain>=2:
        line=after.copy(stack=False);line_sans=[];focus=[]
        for line_move in response_pv[:8]:
            if line_move not in line.legal_moves:break
            if line.is_capture(line_move):
                _victim,capture_square=captured_piece_details(line,line_move)
                if capture_square is not None:focus.append(capture_square)
            line_sans.append(line.san(line_move));line.push(line_move)
        events.append(ReviewEvent("pv_material_gain",
                                  f"Stockfish's line {' '.join(line_sans)} ends about {count_label(pv_gain,'material point')} in your favor.",
                                  "positive","Trace the engine's line: which exchange leaves you ahead in material?",
                                  tuple(focus[:4])))

    # Detect an unchanged friendly piece that lost its defender and can now be
    # captured by the engine's actual best reply.
    old_defender_map=legal_defender_map(before,mover)
    new_defender_map=legal_defender_map(after,mover)
    legal_attacker_map=legal_capture_map(after,opponent)
    old_opponent_defender_map=legal_defender_map(before,opponent,include_pawns=True,include_king=True)
    new_opponent_defender_map=legal_defender_map(after,opponent,include_pawns=True,include_king=True)
    old_mover_capture_map=legal_capture_map(before,mover)
    mover_capture_map=legal_capture_map(after,mover)
    for square,piece in after.piece_map().items():
        if piece.color!=mover or piece.piece_type in (chess.PAWN,chess.KING) or square==move.to_square:continue
        old=before.piece_at(square)
        if old!=piece:continue
        old_defenders=old_defender_map.get(square,set())
        new_defenders=new_defender_map.get(square,set())
        attackers=legal_attacker_map.get(square,set())
        if not attackers:continue
        departed=old_defenders-new_defenders
        lost_only_defender=(len(old_defenders)==1 and len(new_defenders)==0 and move.from_square in departed)
        if lost_only_defender:
            if response_san and response_takes and response_capture_square==square:
                text=f"Moving your {piece_label(mover_piece)} from {chess.square_name(move.from_square)} removes the only defender of your {piece_label(piece)} on {chess.square_name(square)}; {response_san} can capture it."
            else:
                text=f"Moving your {piece_label(mover_piece)} from {chess.square_name(move.from_square)} leaves your {piece_label(piece)} on {chess.square_name(square)} attacked by {count_label(len(attackers),'piece')} and without a defender."
            events.append(ReviewEvent("lost_defender",text,"critical",focus_squares=(square,)));break
        if len(attackers)>len(new_defenders) and not old_defenders and response_san and response_takes and response_capture_square==square:
            events.append(ReviewEvent("hanging_piece",f"Your {piece_label(piece)} on {chess.square_name(square)} is left undefended; {response_san} captures it.","critical",focus_squares=(square,)));break
        if response_san and response_takes and response_capture_square==square and len(attackers)>len(new_defenders):
            events.append(ReviewEvent("hanging_piece",f"Your {piece_label(piece)} on {chess.square_name(square)} is attacked by {count_label(len(attackers),'piece')} and defended by {count_label(len(new_defenders),'piece')}; {response_san} captures it.","critical",focus_squares=(square,)));break

    for square,piece in after.piece_map().items():
        if piece.color!=opponent or piece.piece_type in (chess.PAWN,chess.KING):continue
        if before.piece_at(square)!=piece:continue
        attackers=mover_capture_map.get(square,set());defenders=new_opponent_defender_map.get(square,set())
        if not attackers or len(attackers)<=len(defenders):continue
        old_attackers=old_mover_capture_map.get(square,set())
        old_defenders=old_opponent_defender_map.get(square,set())
        if len(old_attackers)>len(old_defenders):continue
        events.append(ReviewEvent("opponent_hanging_piece",
                                  f"{move_san} leaves the opponent's {piece_label(piece)} on {chess.square_name(square)} attacked by {count_label(len(attackers),'piece')} and defended by {count_label(len(defenders),'piece')}.",
                                  "positive",focus_squares=(move.to_square,square)))

    actual_themes=tactical_themes(before,move)
    if best_move==move:
        removed_defender=removed_defender_event(before,move,best_pv,missed=False)
        if removed_defender:events.append(removed_defender)
    if after.is_checkmate():events.append(ReviewEvent("checkmate",f"{move_san} checkmates.","positive"))
    elif after.is_stalemate():events.append(ReviewEvent("stalemate",f"The game is drawn by stalemate: the side to move has no legal moves, but its king is not in check.","info"))
    elif after.is_insufficient_material():events.append(ReviewEvent("insufficient_material","The game is drawn because neither side has enough material to force checkmate.","info"))
    elif after.is_seventyfive_moves():events.append(ReviewEvent("seventyfive_move_rule","The game is drawn by the seventy-five-move rule: 75 full moves passed without a pawn move or capture.","info"))
    elif after.is_fivefold_repetition():events.append(ReviewEvent("fivefold_repetition","The game is drawn by fivefold repetition: the same position occurred at least five times.","info"))
    elif after.is_check():events.append(ReviewEvent("forcing_check",f"{move_san} gives check and forces the opponent to respond.","positive"))
    if "Fork" in actual_themes:
        targets=[f"{piece_label(target)} on {chess.square_name(square)}" for target,square in fork_targets(before,move)]
        if len(targets)>=2:
            named=" and ".join(targets[:3])
            events.append(ReviewEvent("fork",f"{move_san} forks the {', '.join(targets[:3])}.","positive",
                                      f"Your {move_san} attacks the {named}. Which target must respond first, and what can you win while it does?",
                                      tuple([move.to_square]+[square for _target,square in fork_targets(before,move)[:3]])))
    if "Promotion" in actual_themes:events.append(ReviewEvent("promotion",f"{move_san} promotes the pawn.","positive"))
    opened_targets=newly_opened_slider_targets(before,after,move.to_square)
    discovered_check=next((item for item in opened_targets if item[2].piece_type==chess.KING),None)
    if "Discovered check" in actual_themes and discovered_check:
        slider,source,target,target_square=discovered_check
        events.append(ReviewEvent("discovered_check",f"{move_san} uncovers check from your {piece_label(slider)} on {chess.square_name(source)} to the king on {chess.square_name(target_square)}.","positive",focus_squares=(source,target_square,move.to_square)))
    discovered_target=next((item for item in opened_targets if item[2].piece_type!=chess.KING),None)
    if "Discovered attack" in actual_themes and discovered_target:
        slider,source,target,target_square=discovered_target
        events.append(ReviewEvent("discovered_attack",f"{move_san} opens a line from your {piece_label(slider)} on {chess.square_name(source)} to the {piece_label(target)} on {chess.square_name(target_square)}.","positive",focus_squares=(source,target_square,move.to_square)))
    if "Skewer" in actual_themes:
        line=next(iter(newly_created_skewers(before,after,mover)),None)
        if line:
            slider,source,front,front_square,rear,rear_square=line
            events.append(ReviewEvent("skewer",f"{move_san} points your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)} behind it.","positive",
                                      f"Your {piece_label(slider)} on {chess.square_name(source)} attacks the {piece_label(front)} on {chess.square_name(front_square)} first. If it moves, what happens to the {piece_label(rear)} on {chess.square_name(rear_square)}?",
                                      (source,front_square,rear_square)))
    if before.is_castling(move):events.append(ReviewEvent("castling",f"{move_san} castles and connects the rook to the game.","positive"))

    if best_move!=move:
        best_themes=tactical_themes(before,best_move)
        labels=[name.lower() for name in ("Mate","Fork","Pin","Skewer","Discovered attack","Discovered check","Promotion","Check") if name in best_themes]
        if "Mate" in best_themes:
            events.append(ReviewEvent("missed_mate",f"The engine's best move {before.san(best_move)} forces checkmate.","critical"))
        elif explain_best_difference and "Fork" in best_themes:
            targets=[f"{piece_label(target)} on {chess.square_name(square)}" for target,square in fork_targets(before,best_move)]
            description=f" forks the {', '.join(targets[:3])}" if len(targets)>=2 else " creates a fork"
            prompt=(f"The missed {before.san(best_move)} attacks the {' and '.join(targets[:3])}. Which target must respond first, and what can you win while it does?"
                    if len(targets)>=2 else "Before choosing, which forcing move attacks more than one valuable target?")
            focus=tuple([best_move.from_square,best_move.to_square]+[square for _target,square in fork_targets(before,best_move)[:3]])
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{description}.","warning",prompt,focus))
        elif explain_best_difference and "Skewer" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            line=next(iter(newly_created_skewers(before,best_after,mover)),None)
            if line:
                slider,source,front,front_square,rear,rear_square=line
                detail=f" points your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)} behind it"
                prompt=f"The missed {before.san(best_move)} lines your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)}. What happens to the rear target when the front one moves?"
            else:
                detail=" creates a skewer";prompt="Could a line piece attack a valuable target first and another piece behind it?"
            focus=(source,front_square,rear_square) if line else (best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{detail}.","warning",prompt,focus))
        elif explain_best_difference and "Pin" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            pin=next((item for item in newly_pinned_pieces(before,best_after) if item[2].color!=mover),None)
            if pin:
                pinner,pinner_square,piece,square=pin
                detail=f" pins the opponent's {piece_label(piece)} on {chess.square_name(square)} to its king with your {piece_label(pinner)} on {chess.square_name(pinner_square)}"
                pinned_king=best_after.king(piece.color)
                prompt=f"The missed {before.san(best_move)} pins the {piece_label(piece)} on {chess.square_name(square)} to the king on {chess.square_name(pinned_king)}. What changes if that piece moves?"
                focus=(pinner_square,square,pinned_king) if pinned_king is not None else (pinner_square,square)
            else:
                pin=next(iter(newly_relative_pins(before,best_after,mover)),None)
                if pin:
                    pinner,pinner_square,piece,square,target,target_square=pin
                    detail=f" pins the opponent's {piece_label(piece)} on {chess.square_name(square)} against the {piece_label(target)} on {chess.square_name(target_square)} with your {piece_label(pinner)} on {chess.square_name(pinner_square)}"
                    prompt=f"The missed {before.san(best_move)} lines your {piece_label(pinner)} on {chess.square_name(pinner_square)} through the {piece_label(piece)} on {chess.square_name(square)} to the {piece_label(target)} on {chess.square_name(target_square)}. What is exposed if the pinned piece moves?"
                    focus=(pinner_square,square,target_square)
                else:
                    detail=" creates a pin";prompt="Can a line piece make an enemy piece unsafe to move by exposing its king or a more valuable piece?";focus=(best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{detail}.","warning",prompt,focus))
        elif explain_best_difference and (removed_defender:=removed_defender_event(before,best_move,best_pv)):
            events.append(removed_defender)
        elif explain_best_difference and "Winning material" in best_themes and not best_move.promotion:
            victim,capture_square=captured_piece_details(before,best_move)
            detail=f"takes the {piece_label(victim)} on {chess.square_name(capture_square)}" if victim else "wins material"
            events.append(ReviewEvent("missed_material",f"The engine's best move {before.san(best_move)} {detail}.","warning",focus_squares=(best_move.from_square,best_move.to_square,capture_square)))
        elif explain_best_difference and best_move.promotion:
            promoted=piece_label(chess.Piece(best_move.promotion,mover));best_after=before.copy(stack=False);best_after.push(best_move)
            king=best_after.king(opponent);check_text=f" with check on the king on {chess.square_name(king)}" if best_after.is_check() and king is not None else ""
            prompt=f"The missed {before.san(best_move)} promotes the pawn on {chess.square_name(best_move.from_square)} to a {promoted} on {chess.square_name(best_move.to_square)}. What forcing threats does the new piece create?"
            focus=(best_move.from_square,best_move.to_square)+((king,) if check_text and king is not None else ())
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} promotes the pawn on {chess.square_name(best_move.from_square)} to a {promoted} on {chess.square_name(best_move.to_square)}{check_text}.","warning",prompt,focus))
        elif explain_best_difference and "Discovered attack" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            opened=newly_opened_slider_targets(before,best_after,best_move.to_square)
            target=next((item for item in opened if item[2].piece_type in (chess.QUEEN,chess.ROOK)),None)
            if target:
                slider,source,piece,target_square=target;moved=before.piece_at(best_move.from_square)
                detail=f"moves your {piece_label(moved)} from {chess.square_name(best_move.from_square)}, uncovering your {piece_label(slider)} on {chess.square_name(source)} against the {piece_label(piece)} on {chess.square_name(target_square)}"
                prompt=f"The missed {before.san(best_move)} uncovers your {piece_label(slider)} on {chess.square_name(source)} against the {piece_label(piece)} on {chess.square_name(target_square)}. How can you exploit that line?"
                focus=(best_move.from_square,best_move.to_square,source,target_square)
            else:
                detail="uncovers an attack from a line piece";prompt="Which piece moves out of the way, and what valuable target does the line piece attack?";focus=(best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} {detail}.","warning",prompt,focus))
        elif explain_best_difference and "Check" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            king=best_after.king(opponent);checkers=list(best_after.checkers())
            checker_square=checkers[0] if checkers else best_move.to_square
            checker=best_after.piece_at(checker_square)
            king_name=chess.square_name(king) if king is not None else "the king"
            if checker_square!=best_move.to_square:
                moved=before.piece_at(best_move.from_square)
                detail=f"uncovers check from your {piece_label(checker)} on {chess.square_name(checker_square)} to the king on {king_name} by moving the {piece_label(moved)}"
            else:
                detail=f"gives check from your {piece_label(checker)} on {chess.square_name(checker_square)} to the king on {king_name}"
            prompt=f"The missed {before.san(best_move)} checks the king on {king_name}. Which legal replies meet the threat without losing material?"
            focus=tuple([best_move.from_square,best_move.to_square,checker_square]+([king] if king is not None else []))
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} {detail}.","warning",prompt,focus))
        elif explain_best_difference and labels:
            description=" and ".join(labels)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} creates a {description}.","warning"))
        if (explain_best_difference
                and not any(event.kind in ("missed_mate","missed_tactic","missed_material") for event in events)):
            plan=missed_plan_event(before,move,after,best_move)
            if plan:events.append(plan)

    # Name only pins newly created by the move; include absolute pins to the
    # king and relative pins to a more valuable rook or queen.
    absolute_pins=[(pinner,pinner_square,piece,square,None,None)
                   for pinner,pinner_square,piece,square in newly_pinned_pieces(before,after)]
    relative_pins=[]
    for attacker in (mover,opponent):relative_pins.extend(newly_relative_pins(before,after,attacker))
    pins=absolute_pins+relative_pins
    if pins:
        pinner,pinner_square,piece,square,target,target_square=next(
            (pin for pin in pins if pin[2].color==mover),pins[0])
        who="your" if piece.color==mover else "the opponent's"
        pinner_name=piece_label(pinner);pin_square=chess.square_name(pinner_square)
        piece_name=piece_label(piece);piece_square=chess.square_name(square)
        if target is None:
            pinned_king=after.king(piece.color);target_name="king";target_square_name=chess.square_name(pinned_king) if pinned_king is not None else "its king"
            detail=f"pins {who} {piece_name} on {piece_square} to the {target_name} on {target_square_name}"
            prompt=(f"Your {pinner_name} on {pin_square} pins the {piece_name} on {piece_square} to the king on {target_square_name}. What would happen if the pinned piece moved?"
                    if piece.color!=mover else f"The opponent's {pinner_name} on {pin_square} pins your {piece_name} on {piece_square}. What valuable piece or square is behind it?")
        else:
            target_name=piece_label(target);target_square_name=chess.square_name(target_square)
            detail=f"pins {who} {piece_name} on {piece_square} against the {target_name} on {target_square_name}"
            prompt=(f"Your {pinner_name} on {pin_square} lines up the {piece_name} on {piece_square} with the {target_name} on {target_square_name}. What is exposed if the pinned piece moves?"
                    if piece.color!=mover else f"The opponent's {pinner_name} on {pin_square} lines up your {piece_name} on {piece_square} with the {target_name} on {target_square_name}. What could be lost if it moves?")
        focus=(pinner_square,square)+( (target_square,) if target is not None else ((after.king(piece.color),) if after.king(piece.color) is not None else ()) )
        events.append(ReviewEvent("new_pin",f"The {pinner_name} on {pin_square} {detail}.","warning" if piece.color==mover else "positive",prompt,focus))

    # Explain structural consequences only when this move measurably changes them.
    before_doubled,before_isolated=_pawn_structure(before,mover);after_doubled,after_isolated=_pawn_structure(after,mover)
    new_doubled=after_doubled-before_doubled;new_isolated=after_isolated-before_isolated
    if new_doubled:
        files=", ".join(chr(ord("a")+file)+"-file" for file in sorted(new_doubled))
        events.append(ReviewEvent("pawn_structure",f"The move leaves your pawns doubled on the {files}.","warning"))
    elif new_isolated:
        files=", ".join(chr(ord("a")+file)+"-file" for file in sorted(new_isolated))
        events.append(ReviewEvent("pawn_structure",f"The move leaves a pawn isolated on the {files}.","warning"))

    pressure_before=_king_pressure(before,mover);pressure_after=_king_pressure(after,mover)
    if pressure_after-pressure_before>=2:
        king=after.king(mover)
        events.append(ReviewEvent("king_exposure",f"The move increases attacks around your king on {chess.square_name(king)} from {pressure_before} to {pressure_after} nearby squares.","warning"))
    elif pressure_before-pressure_after>=2:
        king=after.king(mover)
        events.append(ReviewEvent("king_safety",f"The move reduces enemy attacks around your king on {chess.square_name(king)} from {pressure_before} to {pressure_after} nearby squares.","positive"))
    center_before=_center_control(before,mover);center_after=_center_control(after,mover)
    if center_before-center_after>=2:
        events.append(ReviewEvent("center_control",f"The move reduces your attacks on the four central squares from {center_before} to {center_after}.","warning"))
    elif center_after-center_before>=2:
        events.append(ReviewEvent("central_control",f"The move increases your attacks on the four central squares from {center_before} to {center_after}.","positive"))

    if mover_piece and mover_piece.piece_type in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN):
        mobility_before=_piece_mobility(before,mover,move.from_square)
        mobility_after=_piece_mobility(after,mover,move.to_square)
        if mobility_after-mobility_before>=2:
            events.append(ReviewEvent("piece_activity",f"Your {piece_label(mover_piece)} improves its legal mobility from {mobility_before} to {mobility_after} moves.","positive"))
        elif mobility_before-mobility_after>=3:
            events.append(ReviewEvent("restricted_piece",f"Your {piece_label(mover_piece)} becomes more restricted: its legal mobility falls from {mobility_before} to {mobility_after} moves.","warning"))

    home_rank=0 if mover==chess.WHITE else 7
    if mover_piece and mover_piece.piece_type in (chess.KNIGHT,chess.BISHOP) and chess.square_rank(move.from_square)==home_rank and chess.square_rank(move.to_square)!=home_rank:
        events.append(ReviewEvent("development",f"{move_san} develops your {piece_label(mover_piece)} from its starting square.","positive"))

    if (repeated_piece_moves>=3 and mover_piece and before.legal_moves.count()>1
            and not before.is_check() and not before.is_capture(move) and not after.is_check()):
        suffix="th" if 10<=repeated_piece_moves%100<=20 else {1:"st",2:"nd",3:"rd"}.get(repeated_piece_moves%10,"th")
        events.append(ReviewEvent("repeat_piece",f"This is the {repeated_piece_moves}{suffix} move by the same {piece_label(mover_piece)}; check that the reroute has gained something, since repeated moves can cost development time.","context"))

    # The strongest response line can establish a trapped piece: it is under
    # attack and has no legal safe retreat. Do not infer this from geometry alone.
    if response_takes and response_victim and response_victim.piece_type not in (chess.PAWN,chess.KING):
        target_square=response_capture_square
        if len(set(after.attackers(opponent,target_square)))>len(set(after.attackers(mover,target_square))):
            safe_exits=_safe_piece_escapes(after,mover,target_square)
            if safe_exits==0:
                events.append(ReviewEvent("trapped_piece",f"Your {piece_label(response_victim)} on {response_square} has no safe legal escape before the opponent's capture.","critical"))
    return tuple(events)


def review_reflection_prompt(events: tuple[ReviewEvent,...]) -> Optional[str]:
    """Choose one short question grounded in a verified event from this move."""
    prompts={
        "lost_defender":"Before moving a defender, which other piece still depends on it?",
        "hanging_piece":"Before committing, what can the opponent capture immediately?",
        "trapped_piece":"If this piece is attacked next, where can it retreat safely?",
        "missed_mate":"Start with forcing checks: which king replies can you rule out?",
        "missed_tactic":"Before a quiet move, did you check captures, checks, and direct threats?",
        "missed_plan":"What concrete positional improvement does the stronger move create?",
        "missed_material":"After the forcing line, which loose piece can still be won?",
        "opponent_mate":"Which flight square or defender could stop the mating net?",
        "back_rank_mate":"Could a flight square or back-rank defender prevent mate?",
        "opponent_capture":"After the strongest reply, which of your pieces is under attack?",
        "pv_material_loss":"Follow the exchange sequence: where does the material balance turn?",
        "pv_material_gain":"Trace the engine's line: which exchange leaves you ahead in material?",
        "fork":"Which two valuable targets are hit, and which one must respond first?",
        "discovered_check":"Which line piece gives check when the blocker moves?",
        "discovered_attack":"Which line opened, and what target is now attacked?",
        "skewer":"Which front piece must move, exposing the target behind it?",
        "sound_sacrifice":"If the opponent accepts the sacrifice, where does your compensation come from?",
        "removed_defender":"Which capture removes the defender, and what valuable target becomes vulnerable?",
        "only_good_move":"What makes the alternatives fail to hold equality in this position?",
        "turnaround":"Which feature of this position lets this move turn a likely loss into a drawable game?",
        "new_pin":"What valuable piece or square lies behind the pinned piece?",
        "king_exposure":"Which file or diagonal opened toward your king after this move?",
        "king_safety":"Which escape square or line is safer for your king now?",
        "pawn_structure":"Will this pawn move leave a weakness that cannot move again?",
        "center_control":"Which central squares did this move stop contesting?",
        "central_control":"Which central squares does this move help you control?",
        "repeat_piece":"What concrete threat or improvement pays for moving this piece again?",
        "restricted_piece":"What route could give this piece a useful square?",
        "piece_activity":"Where can this more active piece go next?",
        "development":"What new squares or plans does this developed piece enable?",
        "castling":"How does castling help connect your rook to the plan?",
        "promotion":"What new threats does the promoted piece create?",
        "checkmate":"Which escape squares make this checkmate work?",
    }
    priorities={"lost_defender":0,"hanging_piece":0,"trapped_piece":0,"missed_mate":0,
                "missed_tactic":1,"missed_plan":2,"opponent_mate":1,"back_rank_mate":1,"opponent_capture":2,
                "pv_material_loss":2,"pv_material_gain":1,"fork":2,"discovered_check":2,"discovered_attack":2,
                "skewer":2,"sound_sacrifice":2,"new_pin":3,"king_exposure":3,"pawn_structure":3,
                "center_control":3,"central_control":3,"repeat_piece":3,"king_safety":4,
                "restricted_piece":4,"piece_activity":4,"development":4,"castling":4,
                "promotion":2,"checkmate":1,"missed_material":2,"removed_defender":2,
                "only_good_move":1}
    candidates=sorted((event for event in events if event.kind in prompts),
                      key=lambda event:priorities.get(event.kind,5))
    return (candidates[0].prompt or prompts[candidates[0].kind]) if candidates else None


def review_focus_squares(events: tuple[ReviewEvent,...]) -> tuple[chess.Square,...]:
    """Choose the most useful verified target squares to mark on the review board."""
    priority={"lost_defender":0,"hanging_piece":0,"trapped_piece":0,"missed_mate":0,
              "opponent_mate":0,"back_rank_mate":0,"opponent_capture":1,"pv_material_loss":1,"pv_material_gain":1,
              "missed_tactic":1,"missed_material":1,"fork":1,"discovered_check":1,
              "discovered_attack":1,"skewer":1,"new_pin":1,"removed_defender":1,
              "only_good_move":1,"turnaround":1,"missed_plan":2,"capture":3}
    ordered=sorted((event for event in events if event.focus_squares),
                   key=lambda event:priority.get(event.kind,4))
    if not ordered:return ()
    return tuple(dict.fromkeys(square for square in ordered[0].focus_squares if square is not None))[:4]


def review_practice_hint(board: chess.Board, best_move: chess.Move,
                        best_pv: tuple[chess.Move,...] = (), level: int = 1) -> str:
    """Give progressively more specific, non-spoiling hints from the engine's move."""
    if best_move not in board.legal_moves:
        return "Compare forcing checks and captures first; then improve your least active piece."
    themes=tactical_themes(board,best_move)
    after=board.copy(stack=False);after.push(best_move)
    if level>=2:
        if "Fork" in themes:
            targets=fork_targets(board,best_move)
            names=[f"the {piece_label(piece)} on {chess.square_name(square)}" for piece,square in targets[:3]]
            if len(names)>=2:return f"The targets are {' and '.join(names)}. Look for one move that attacks both at once."
        if "Discovered check" in themes or "Discovered attack" in themes:
            opened=newly_opened_slider_targets(board,after,best_move.to_square)
            target=next((entry for entry in opened if entry[2].piece_type==chess.KING),None) if "Discovered check" in themes else None
            target=target or next((entry for entry in opened if entry[2].piece_type in (chess.QUEEN,chess.ROOK)),None)
            if target:
                slider,source,piece,square=target
                target_name="the king" if piece.piece_type==chess.KING else f"the {piece_label(piece)}"
                return (f"Watch the line from your {piece_label(slider)} on {chess.square_name(source)} "
                        f"toward {target_name} on {chess.square_name(square)}. Which blocker can move to open it?")
        if "Skewer" in themes:
            line=next(iter(newly_created_skewers(board,after,board.turn)),None)
            if line:
                slider,source,front,front_square,rear,rear_square=line
                return (f"Look at the line from your {piece_label(slider)} on {chess.square_name(source)} "
                        f"through the {piece_label(front)} on {chess.square_name(front_square)} toward "
                        f"the {piece_label(rear)} on {chess.square_name(rear_square)} behind it.")
        if "Pin" in themes:
            pin=next((entry for entry in newly_pinned_pieces(board,after) if entry[2].color!=board.turn),None)
            if pin:
                pinner,pinner_square,piece,square=pin
                return (f"A line from your {piece_label(pinner)} on {chess.square_name(pinner_square)} "
                        f"can pin the {piece_label(piece)} on {chess.square_name(square)}. What valuable target lies behind it?")
            relative_pin=next(iter(newly_relative_pins(board,after,board.turn)),None)
            if relative_pin:
                pinner,pinner_square,piece,square,target,target_square=relative_pin
                return (f"Look for a line from your {piece_label(pinner)} on {chess.square_name(pinner_square)} "
                        f"through the {piece_label(piece)} on {chess.square_name(square)} toward "
                        f"the {piece_label(target)} on {chess.square_name(target_square)}.")
        if "Promotion" in themes:
            return f"The pawn on {chess.square_name(best_move.from_square)} is ready to promote. Check whether the promotion also creates check or a direct threat."
        if "Winning material" in themes:
            victim,square=captured_piece_details(board,best_move)
            if victim and square is not None:
                return f"The target is the {piece_label(victim)} on {chess.square_name(square)}. Count its defenders and the recapture before taking it."
        if "Mate" in themes:
            return "Start with checks that restrict the king's escape squares; compare the opponent's only legal replies."
        if "Check" in themes:
            checkers=list(after.checkers())
            checker=after.piece_at(checkers[0]) if checkers else None
            square=chess.square_name(checkers[0]) if checkers else chess.square_name(best_move.to_square)
            piece_text=f"your {piece_label(checker)}" if checker else "your moved piece"
            return f"The checking piece is {piece_text} on {square}. Calculate each legal king reply before choosing your move."
        positional_details={
            "central_control":"Compare which candidate move increases your attacks on d4, e4, d5, and e5.",
            "development":"Look at the pieces still on their starting squares; which one can join the center with tempo?",
            "castling":"Check whether castling is legal and how it would secure your king while activating the rook.",
            "king_safety":"Look for the open files and diagonals pointing toward your king, then find a move that blocks or reduces that pressure.",
            "piece_activity":"Find your least active piece and compare the useful squares it can reach after the move.",
            "new_pin":"Look for a line piece that can tie an enemy piece to its king or a more valuable piece.",
        }
        events=analyze_review_events(board,best_move,after,best_move,best_pv=best_pv[:10])
        for event in events:
            if event.kind in positional_details:return positional_details[event.kind]
    tactical_hints=(
        ("Mate","Look for a forcing check that leaves the king with no escape."),
        ("Fork","Look for a move that attacks two valuable targets at once."),
        ("Discovered check","Can moving a blocker uncover check from a line piece?"),
        ("Discovered attack","Can moving a blocker uncover an attack on a valuable piece?"),
        ("Skewer","Can you line up a valuable front piece with another target behind it?"),
        ("Pin","Look for a line piece that makes an enemy piece unsafe to move."),
        ("Promotion","Can a pawn advance with promotion, or create an unavoidable promotion threat?"),
        ("Winning material","Look for a forcing capture or sequence that wins material."),
        ("Check","Try forcing checks first and see whether the reply leaves a second threat."),
    )
    for theme,hint in tactical_hints:
        if theme in themes:return hint
    response=best_pv[1] if len(best_pv)>1 and best_pv[1] in after.legal_moves else None
    response_pv=best_pv[2:10] if response else ()
    events=analyze_review_events(board,best_move,after,best_move,response,best_pv[:10],response_pv=response_pv)
    positional_hints={
        "central_control":"Look for a move that expands your control of the central squares.",
        "development":"Look for a developing move that gives a piece more useful squares.",
        "castling":"Look for a way to secure your king and connect your rook to the game.",
        "king_safety":"Look for a move that reduces the opponent's pressure around your king.",
        "piece_activity":"Improve the mobility of your least active piece.",
        "new_pin":"Look for a line that ties an enemy piece to a more valuable target behind it.",
    }
    for event in events:
        if event.kind in positional_hints:return positional_hints[event.kind]
    return "Compare forcing checks and captures first; if none work, improve your least active piece."


def generate_review_commentary(verdict: str, san: str, best_san: str, before_cp: int, after_cp: int,
                               loss: int, events: tuple[ReviewEvent,...], response: str = "",
                               best_reason: str = "", best_pv: str = "",
                               expected_loss: Optional[float] = None,
                               variation_seed: int = 0) -> str:
    """Turn verified events into concise coaching text; use eval-only language as fallback."""
    priority={"lost_defender":0,"hanging_piece":0,"trapped_piece":1,"missed_mate":0,"missed_tactic":1,"missed_plan":2,
              "opponent_capture":2,"pv_material_loss":1,"pv_material_gain":1,"opponent_mate":0,"back_rank_mate":0,"opponent_check":4,"new_pin":2,
              "fork":1,"discovered_attack":1,"forcing_check":2,"checkmate":0,"promotion":2,
              "discovered_check":1,"skewer":1,"removed_defender":1,"only_good_move":1,"turnaround":1,
              "pawn_structure":3,"king_exposure":3,"king_safety":2,"center_control":4,"central_control":3,"restricted_piece":3,
              "piece_activity":3,"development":3,"castling":3,"sound_sacrifice":0,"repeat_piece":5}
    ordered=sorted(events,key=lambda event:priority.get(event.kind,6))
    voice_seed=(sum(ord(char) for char in san+best_san)
                +sum(ord(char) for event in ordered for char in event.kind)
                +max(0,int(variation_seed)))
    saturated_gap=(f"The expected-outcome estimate is nearly flat, but Stockfish's evaluation shifts "
                   f"{loss/100:.1f} pawns against the side that moved; {best_san} keeps more of the position.")
    show_saturated_gap=(expected_loss is not None and expected_loss<=.01 and loss>=250
                        and best_san and best_san!=san)
    mobility_alternative=any(event.kind=="missed_plan" and "legal moves" in event.text for event in ordered)
    concrete=[event.text for event in ordered if event.severity in ("critical","warning") and event.kind!="opponent_check"
              and not (mobility_alternative and event.kind=="restricted_piece")]
    if verdict=="Brilliant":
        sacrifice=next((event.text for event in ordered if event.kind=="sound_sacrifice"),None)
        if sacrifice:return f"Brilliant: {sacrifice}"
        return (f"Unverified move: Stockfish did not confirm a concrete sacrifice for {san}; "
                "do not label it Brilliant.")
    if verdict in ("Blunder","Mistake","Inaccuracy","Miss"):
        if concrete:
            cause=" ".join(concrete[:2])
            openings=(f"{verdict}: {cause}",
                      f"{verdict}: The line after {san} shows the key problem: {cause}",
                      f"{verdict}: Watch the opponent's reply. {cause}",
                      f"{verdict}: This is where the position turns. {cause}",
                      f"{verdict}: The move's concrete problem is clear in the line. {cause}")
            explanation=openings[voice_seed%len(openings)]
            if best_san and best_san!=san and not any(event.kind in ("missed_mate","missed_tactic","missed_plan") for event in ordered):
                alternatives=(f"The stronger alternative was {best_san}.",
                              f"{best_san} avoids this problem.",
                              f"Stockfish prefers {best_san} here.")
                explanation+=f" {alternatives[(voice_seed//len(openings))%len(alternatives)]}"
            return explanation
        return (f"{verdict}: Stockfish prefers {best_san}; the evaluation for the mover shifts from "
                f"{eval_text(before_cp)} to {eval_text(after_cp)} ({loss/100:.1f} pawns by the engine's estimate). "
                "This search did not confirm a specific tactical cause, so treat this as an evaluation-based warning.")
    if verdict=="Book":return f"Book move: {san} follows the loaded local opening book."
    if verdict=="Forced":return f"Forced: {san} was the only legal move."
    positive=[event.text for event in ordered if event.severity=="positive"]
    contextual=[event.text for event in ordered if event.severity=="context"]
    if positive or contextual:
        details=positive[:2] or contextual[:1]
        detail_text=" ".join(details)
        lead_event=next((event for event in ordered if event.severity=="positive"),None)
        # Keep reviews reproducible while avoiding the same stock sentence on
        # every move. The wording is selected from the SAN and verified themes.
        idea_seed=(sum(ord(char) for char in (lead_event.kind if lead_event else ""))
                   +max(0,int(variation_seed)))
        idea_leads={
            "fork":"The double attack is the key idea.",
            "pv_material_gain":"The engine's line confirms a concrete material gain.",
            "skewer":"The more valuable front piece is under attack, with another target behind it.",
            "discovered_attack":"Moving the blocker exposes a target to the line piece.",
            "discovered_check":"The discovered check forces the king to respond.",
            "removed_defender":"Removing that defender is the tactical point.",
            "new_pin":"The pin ties the opponent's piece to a more valuable target.",
            "sound_sacrifice":"The sacrifice is backed by the engine's continuation.",
            "promotion":"The pawn's promotion changes the position immediately.",
            "checkmate":"The move ends the game with a forced mate.",
            "forcing_check":"The check keeps the opponent on the defensive.",
            "king_safety":"This gives your king a safer position.",
            "central_control":"This strengthens your control of the center.",
            "development":"This gets a new piece into the game.",
            "piece_activity":"The improvement in piece activity is the main gain.",
            "castling":"This secures the king and brings the rook closer to play.",
            "pawn_structure":"The pawn structure is the lasting feature of this move.",
        }
        idea_lead=idea_leads.get(lead_event.kind,"This creates a concrete idea.") if lead_event else ""
        if positive and verdict=="Best":
            if best_san==san:
                lead=("Stockfish's top choice is the move you played.",
                      "You found the engine's preferred move.",
                      "The engine's first choice is yours.",
                      "Your move matches its leading line.",
                      "This is the engine's top-scoring option.")[idea_seed%5]
            else:
                lead=("This is one of Stockfish's strongest choices.",
                      "Stockfish also rates this among its leading options.",
                      "The engine considers this a top-tier alternative.",
                      "Your choice stays near the top of the engine's list.",
                      "The move is close to the engine's first choice.")[idea_seed%5]
            return f"Best: {lead} {detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        if positive and verdict=="Excellent":
            closeness=("It stays close to Stockfish's top choice. ",
                       "You keep nearly all the value of the best line. ",
                       "The engine's preferred move is only a small step ahead. ",
                       "The evaluation remains close to its leading line. ",
                       "Only a little expected score separates this from the best move. ")[idea_seed%5]
            return f"Excellent: {idea_lead} {closeness}{detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        if positive and verdict=="Good":
            value_note=("It keeps most of the position's value. ",
                        "You preserve most of the expected score available here. ",
                        "Most of the position's expected score remains. ",
                        "The move gives up only a small part of the engine's estimate. ",
                        "You keep most of the value, though Stockfish found a little more. ")[idea_seed%5]
            return f"Good: {idea_lead} {value_note}{detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        return f"{verdict}: "+detail_text+(f" {saturated_gap}" if show_saturated_gap else "")
    if best_reason and best_san!=san:
        return f"{verdict}: {san} is playable, while {best_san} is the engine's top choice. {best_reason}"+(f" {saturated_gap}" if show_saturated_gap else "")
    line=f" The engine's continuation is {best_pv}." if best_pv else ""
    response_line=f" The opponent's strongest reply is {response}." if response else ""
    return f"{verdict}: {san} keeps the position close to the engine's preferred evaluation.{response_line}{line}"+(f" {saturated_gap}" if show_saturated_gap else "")


def game_coach_summary(items: list[ReviewMove], user_color: chess.Color) -> str:
    """Summarize patterns seen in this game only; do not infer player traits."""
    own=[item for item in items if (item.side=="White") == (user_color==chess.WHITE)]
    if not own:return "No moves from your side were available for a game-specific summary."
    weakness_map={"lost_defender":"pieces left without their defenders","hanging_piece":"undefended pieces",
                  "trapped_piece":"pieces with no safe retreat","missed_tactic":"missed tactical chances",
                  "missed_plan":"positional improvements left unused",
                  "missed_mate":"missed forced mates","back_rank_mate":"back-rank king safety","missed_material":"missed chances to win material",
                  "pv_material_loss":"material lost in the engine line","new_pin":"pieces left pinned","king_exposure":"king exposure","pawn_structure":"pawn-structure weaknesses",
                  "repeat_piece":"repeated moves with the same piece","restricted_piece":"restricted piece activity",
                  "opponent_capture":"allowing tactical captures","pv_material_loss":"material lost in the engine line",
                  "opponent_mate":"allowing checkmate","back_rank_mate":"back-rank king safety"}
    weakness_counts={};error_verdicts=("Miss","Inaccuracy","Mistake","Blunder")
    for item in own:
        move_weaknesses=set()
        for event in item.events:
            if (event.kind in weakness_map and item.verdict in error_verdicts
                    and event.severity in ("critical","warning","context")):
                move_weaknesses.add(event.kind)
        for kind in move_weaknesses:
            weakness_counts[kind]=weakness_counts.get(kind,0)+1
    weaknesses=sorted(((count,kind,weakness_map[kind]) for kind,count in weakness_counts.items()),reverse=True)
    recurring=[entry for entry in weaknesses if entry[0]>=2]
    voice_seed=sum(ord(char) for item in own for char in item.san)+sum(item.ply for item in own)
    def example_for(kind):
        candidates=[item for item in own if item.verdict in error_verdicts
                    and any(event.kind==kind and event.severity in ("critical","warning","context") for event in item.events)]
        if not candidates:return ""
        example=max(candidates,key=lambda item:(getattr(item,"expected_loss",0.0),-item.ply))
        event=next(event for event in example.events if event.kind==kind and event.severity in ("critical","warning","context"))
        return f" The clearest example was {example.side} {example.san} (move {(example.ply+1)//2}): {event.text}"
    coaching_tip={
        "lost_defender":"Before committing to an attack, check whether each attacked piece still has enough defenders.",
        "hanging_piece":"Before moving, scan for opponent captures that would take an undefended piece.",
        "trapped_piece":"Before advancing a piece, identify at least one safe retreat square.",
        "missed_tactic":"When the opponent moves, check captures, checks, and direct threats before choosing a plan.",
        "missed_plan":"Compare the engine's plan with yours: did it improve the center, king safety, development, or activity?",
        "missed_mate":"Look for forcing checks first, then calculate whether the king has any legal escape.",
        "back_rank_mate":"Leave a flight square or a defender near your king when the back rank is exposed.",
        "missed_material":"After every forcing move, look for loose pieces and tactics that win material.",
        "king_exposure":"Before pushing pawns near your king, check which files and diagonals will open.",
        "pawn_structure":"Check whether a pawn move creates a lasting weakness before making it.",
        "repeat_piece":"After moving the same piece again, ask what concrete threat or improvement the reroute achieves.",
        "restricted_piece":"Look for a route that gives your least active piece a useful square.",
        "opponent_capture":"Before finalizing a move, test the opponent's forcing captures and checks.",
    }
    if recurring:
        _,kind,_=recurring[0]
        example_text=example_for(kind)
        lead=("Your clearest recurring lesson was ","A pattern to work on from this game: ",
              "The recurring theme in this game was ")[voice_seed%3]
        weakness_text=lead+", ".join(label for _,_,label in recurring[:2])+"."+example_text+" "+coaching_tip.get(kind,"Review the critical moments and calculate the opponent's strongest reply.")
    elif weaknesses:
        _,kind,label=weaknesses[0]
        example_text=example_for(kind)
        lead=("One useful lesson from this game is to watch for ",
              "This game offered a chance to improve around ",
              "Keep an eye on ")[voice_seed%3]
        weakness_text=lead+label+"."+example_text+" "+coaching_tip.get(kind,"Review this moment and compare your move with the engine's strongest reply.")
    else:
        major=[item for item in own if item.verdict in ("Miss","Blunder","Mistake","Inaccuracy")]
        if major:
            worst=max(major,key=lambda item:item.expected_loss)
            weakness_text=f"The largest unexplained evaluation swing was {worst.san} ({worst.verdict}); revisit the engine line for that move."
        else:weakness_text="The review found no repeated tactical or positional weakness in your moves."
    strengths=[]
    tactical_moves=[item for item in own if has_review_tactical_idea(item)]
    quality_moves=sum(item.verdict in ("Best","Excellent","Good","Great","Brilliant") for item in own)
    accurate=sum(item.verdict in ("Best","Excellent","Great","Brilliant") for item in own)
    if tactical_moves:
        example=max(tactical_moves,key=lambda item:(getattr(item,"expected_loss",0.0),-item.ply))
        event=next(event for event in example.events
                   if event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive")
        tactical_count="a tactical idea in one move" if len(tactical_moves)==1 else f"tactical ideas in {len(tactical_moves)} moves"
        tactic_phrasing=(f"You found {tactical_count}, including {review_move_notation(example)}: {event.text}",
                         f"A tactical highlight was {review_move_notation(example)}: {event.text}",
                         f"You spotted a concrete tactic on {review_move_notation(example)}: {event.text}")
        strengths.append(tactic_phrasing[voice_seed%len(tactic_phrasing)])
    if quality_moves:
        quality_count="one of your moves" if quality_moves==1 else f"{quality_moves} of your moves"
        accuracy_count="one reached" if accurate==1 else f"{accurate} reached"
        accuracy_note=(f"; {accuracy_count} Excellent or above" if accurate
                       else "; none reached Excellent or above")
        accuracy_phrasing=(f"Stockfish rated {quality_count} Good or better{accuracy_note}.",
                           f"You earned Good-or-better ratings on {quality_count}{accuracy_note}.",
                           f"{quality_count.capitalize()} earned a Good-or-better label{accuracy_note}.")
        strengths.append(accuracy_phrasing[(voice_seed+1)%len(accuracy_phrasing)])
    if strengths:
        intro=("A strength in this game: ","One thing you did well: ","A bright spot from this game: ")[voice_seed%3]
        return weakness_text+" "+intro+" ".join(strengths)
    return weakness_text


def personalized_review_coach(items: list[ReviewMove], user_color: chess.Color,
                             saved_game_stats: list[dict]) -> str:
    """Connect this game's lesson to themes verified across separate saved games."""
    summary=game_coach_summary(items,user_color)
    recurring=recurring_review_themes(saved_game_stats,limit=1)
    if not recurring:return summary
    theme,games=recurring[0]
    return (f"{summary} Across your saved reviews, the recurring pattern is “{theme}”; "
            f"it appeared in {games} separate games. Watch for it in your next game.")


def player_review_overview(items: list[ReviewMove], user_color: chess.Color) -> str:
    """Summarize player-only move quality by phase and their biggest swing."""
    own=[item for item in items if (item.side=="White") == (user_color==chess.WHITE)]
    if not own:return "No moves from your side were available in this review."
    accuracy=review_accuracy(own)
    biggest=max(own,key=lambda item:item.expected_loss)
    phase_scores=[]
    for phase in ("Opening","Middlegame","Endgame"):
        phase_moves=[item for item in own if getattr(item,"phase","")==phase]
        if phase_moves:
            score=review_accuracy(phase_moves)
            phase_scores.append(f"{phase} {score:.0f}% ({len(phase_moves)} moves)")
    phase_text=" · By phase: "+", ".join(phase_scores) if phase_scores else ""
    return (f"Your move quality: {accuracy:.0f}%{phase_text} · Your biggest swing was your {biggest.san} "
            f"({biggest.verdict}, {biggest.expected_loss*100:.1f} pp expected score lost).")


def review_filter_indices(items: list[ReviewMove], total_plies: int, filter_name: str,
                          player_color: chess.Color, start_turn: chess.Color,
                          side_filter: str = "Both sides") -> list[int]:
    """Return visible zero-based ply indexes for the review move-list filter."""
    visible=[]
    phase_filters={"Opening","Middlegame","Endgame"}
    analysis_filters={"Errors","Missed chances","Good moves","Brilliant / Great",
                      "Book moves","Tactical ideas"}|phase_filters
    for index in range(total_plies):
        item=items[index] if index<len(items) else None
        mover=(start_turn if index%2==0 else not start_turn)
        if (filter_name=="My moves" or side_filter=="My moves") and mover!=player_color:continue
        if side_filter=="White" and mover!=chess.WHITE:continue
        if side_filter=="Black" and mover!=chess.BLACK:continue
        if filter_name in analysis_filters and item is None:continue
        if item and filter_name in phase_filters and item.phase!=filter_name:continue
        if item and filter_name=="Errors" and item.verdict not in ("Inaccuracy","Mistake","Blunder"):continue
        if item and filter_name=="Missed chances" and item.verdict!="Miss":continue
        if item and filter_name=="Good moves" and item.verdict not in ("Brilliant","Great","Best","Excellent","Good","Book"):continue
        if item and filter_name=="Brilliant / Great" and item.verdict not in ("Brilliant","Great"):continue
        if item and filter_name=="Book moves" and not item.book_status.startswith("Book move"):continue
        if filter_name=="Tactical ideas" and not (item and has_review_tactical_idea(item)):continue
        visible.append(index)
    return visible


def review_row_ply(visible_plies: list[int], row: int) -> Optional[int]:
    """Map a selected review row to its game ply, ignoring cleared selections."""
    return visible_plies[row] if 0<=row<len(visible_plies) else None


def review_practice_moves(items: list[ReviewMove], player_color: chess.Color) -> list[ReviewMove]:
    """Select only the player's own reviewed errors for the default retry session."""
    player_side="White" if player_color==chess.WHITE else "Black"
    errors={"Miss","Inaccuracy","Mistake","Blunder"}
    return [item for item in items if item.side==player_side and item.verdict in errors]


def rank_review_moments(items: list[ReviewMove], limit: int = 8) -> list[ReviewMove]:
    """Select the most consequential review moments, then return them in game order."""
    labels={"Brilliant":.18,"Great":.10,"Miss":.04,"Blunder":0.0,"Mistake":0.0,"Inaccuracy":0.0}
    limit=max(0,limit)
    if not limit:return []
    terminal_kinds={"checkmate","stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"}
    is_terminal=lambda item:any(event.kind in terminal_kinds
                                for event in getattr(item,"events",()))
    is_tactic=has_review_tactical_idea
    endings=[item for item in items if is_terminal(item)]
    candidates=[item for item in items if (item.verdict in labels or is_tactic(item)) and not is_terminal(item)]
    def score(item):
        event_bonus=.07 if is_tactic(item) else 0.0
        return item.expected_loss+labels.get(item.verdict,0.0)+event_bonus
    ranked=sorted(candidates,key=lambda item:(score(item),-item.ply),reverse=True)[:limit]
    if endings:ranked=(ranked[:limit-1]+[max(endings,key=lambda item:item.ply)])
    return sorted(ranked,key=lambda item:item.ply)


def review_moment_tags(items: list[ReviewMove], limit: int = 8) -> dict[int,list[str]]:
    """Map curated summary moments to their classifications and verified events."""
    result={}
    event_tags={
        "checkmate":"Checkmate","stalemate":"Stalemate","insufficient_material":"Insufficient material",
        "seventyfive_move_rule":"75-move draw","fivefold_repetition":"Fivefold repetition",
        "missed_mate":"Missed mate","opponent_mate":"Mate allowed","back_rank_mate":"Back-rank danger",
        "missed_tactic":"Missed tactic","missed_material":"Missed material","opponent_capture":"Loose piece",
        "pv_material_loss":"Material loss in line","pv_material_gain":"Material won in line",
        "only_good_move":"Only move","turnaround":"Turnaround","sound_sacrifice":"Sound sacrifice",
        "fork":"Fork","opponent_hanging_piece":"Opponent piece left hanging",
        "skewer":"Skewer","discovered_attack":"Discovered attack",
        "discovered_check":"Discovered check","removed_defender":"Defender removed",
        "lost_defender":"Lost defender","hanging_piece":"Hanging piece","trapped_piece":"Trapped piece",
        "missed_plan":"Positional chance","king_exposure":"King exposed","pawn_structure":"Pawn weakness",
        "restricted_piece":"Restricted piece","forcing_check":"Forcing check","promotion":"Promotion",
        "king_safety":"King secured","central_control":"Center control","piece_activity":"Piece activity",
        "development":"Development","castling":"Castling",
    }
    priority={"checkmate":0,"missed_mate":1,"opponent_mate":1,"back_rank_mate":1,
              "lost_defender":2,"hanging_piece":2,"trapped_piece":2,"opponent_capture":2,
              "pv_material_loss":2,"sound_sacrifice":2,"turnaround":2,"only_good_move":2,
              "opponent_hanging_piece":3,
              "missed_tactic":3,"missed_material":3,"king_exposure":3,
              "pv_material_gain":3,"fork":4,"skewer":4,"discovered_check":4,
              "discovered_attack":4,"removed_defender":4,"new_pin":4,
              "missed_plan":5,"pawn_structure":5,"restricted_piece":5,"opponent_check":5}
    for item in rank_review_moments(items,limit):
        labels=[]
        if item.verdict in ("Brilliant","Great","Miss","Blunder","Mistake","Inaccuracy"):
            labels.append(item.verdict)
        events=getattr(item,"events",())
        candidates=sorted((event for event in events if (event.severity in ("positive","critical","warning")
                           or event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"})
                           and (event.kind in event_tags or event.kind=="new_pin")),
                          key=lambda event:priority.get(event.kind,6))
        if candidates:
            event=candidates[0]
            tag=("Checkmate" if event.kind=="checkmate" else
                 "Pin created" if event.kind=="new_pin" and event.severity=="positive" else
                 "Piece pinned" if event.kind=="new_pin" else event_tags[event.kind])
            labels.append(tag)
        if has_review_tactical_idea(item):
            if not any(label in {"Fork","Skewer","Discovered attack","Discovered check",
                                 "Defender removed","Sound sacrifice","Material won in line","Pin created"}
                       for label in labels):
                labels.append("Tactical idea")
        result[item.ply]=labels or [item.verdict]
    return result


def review_moment_impact(item: ReviewMove) -> str:
    """Show point loss only when it is a meaningful part of this moment."""
    points=max(0.0,float(item.expected_loss))*100
    return f"{points:.1f} pp expected score lost" if points>=.05 else ""


def annotate_review_pgn(game: chess.pgn.Game, moves: list[chess.Move], items: list[ReviewMove],
                        user_color: Optional[chess.Color] = None) -> chess.pgn.Game:
    """Attach review comments and best-move side variations to a PGN game."""
    node=game;board=game.board()
    for index,move in enumerate(moves):
        item=items[index] if index<len(items) else None
        parent=node
        node=parent.add_variation(move)
        if item and item.best_move in board.legal_moves and item.best_move!=move:
            variation=parent.add_variation(item.best_move,comment=f"Engine choice: {item.best_san}")
            board.push(item.best_move)
            for pv_move in item.best_pv_moves[1:]:
                if pv_move not in board.legal_moves:break
                board.push(pv_move)
                if pv_move not in variation.board().legal_moves:break
                variation=variation.add_variation(pv_move)
            board=parent.board()
        if item:
            comment=review_text_for_viewer(item.explanation.strip(),item,user_color)
            if not comment.casefold().startswith(item.verdict.casefold()+":"):
                comment=f"{item.verdict}: {comment}"
            parts=[comment]
            if item.best_san and item.best_san!=item.san:parts.append(f"Engine choice: {item.best_san}")
            if item.pv:parts.append(f"Principal variation: {item.pv}")
            node.comment=" ".join(parts)
        board.push(move)
    return game


class EngineWorker(QObject):
    ready = pyqtSignal(dict)
    failed = pyqtSignal(str)
    bot_move = pyqtSignal(object)
    best_move = pyqtSignal(object)
    position_eval = pyqtSignal(int)
    review_progress = pyqtSignal(int, int, int, object)
    review_done = pyqtSignal(int, object, bool)
    practice_analysis_done = pyqtSignal(object)
    explore_done = pyqtSignal(object)
    puzzle_progress = pyqtSignal(int, int, int)
    puzzle_done = pyqtSignal(int, object, str)
    operation_error = pyqtSignal(str)
    syzygy_status = pyqtSignal(str, str, bool)

    def __init__(self,syzygy_path=""):
        super().__init__()
        self.engine: Optional[chess.engine.SimpleEngine] = None
        self.option_names = set()
        self.syzygy_path=syzygy_path
        self.syzygy_active=False
        self._elo = MIN_ELO
        self._uci_elo_min = MIN_ELO
        self._uci_elo_max = MAX_ELO
        self._multipv_max = 8
        self.review_cache=OrderedDict()
        self.review_cache_limit=384
        self.stop_event = threading.Event()
        self.review_cancel_event = threading.Event()

    @pyqtSlot()
    def start_engine(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH, timeout=10)
            self.option_names = set(self.engine.options)
            if "UCI_Elo" in self.option_names:
                option=self.engine.options["UCI_Elo"]
                self._uci_elo_min=max(MIN_ELO,int(option.min or MIN_ELO))
                self._uci_elo_max=min(MAX_ELO,int(option.max or MAX_ELO))
            if "MultiPV" in self.option_names:
                self._multipv_max=max(1,min(8,int(self.engine.options["MultiPV"].max or 8)))
            options = {}
            if "UCI_LimitStrength" in self.option_names: options["UCI_LimitStrength"] = True
            if "UCI_Elo" in self.option_names: options["UCI_Elo"] = self._uci_elo_min
            if "Skill Level" in self.option_names: options["Skill Level"] = 0
            if options: self.engine.configure(options)
            syzygy_error=""
            if self.syzygy_path:
                if "SyzygyPath" not in self.option_names:
                    syzygy_error="This Stockfish build does not support Syzygy tablebases."
                elif not Path(self.syzygy_path).is_dir():
                    syzygy_error=f"Tablebase folder not found: {self.syzygy_path}"
                else:
                    try:self._apply_syzygy_path(self.syzygy_path)
                    except Exception as exc:syzygy_error=f"Could not configure Syzygy tablebases: {exc}"
            self.ready.emit({"options": self.option_names,"syzygy_active":self.syzygy_active,
                             "syzygy_error":syzygy_error})
        except Exception as exc:
            self.failed.emit(f"Could not start Stockfish at {STOCKFISH_PATH}: {exc}")

    def _apply_syzygy_path(self,path):
        if "SyzygyPath" not in self.option_names:
            raise RuntimeError("This Stockfish build does not support Syzygy tablebases")
        if path and (not Path(path).is_dir()
                     or (not any(Path(path).glob("*.rtbw"))
                         and not any(Path(path).glob("*.rtbz")))):
            raise RuntimeError(f"No .rtbw or .rtbz Syzygy files found in {path}")
        options={"SyzygyPath":path}
        if "SyzygyProbeLimit" in self.option_names:options["SyzygyProbeLimit"]=7
        if "Syzygy50MoveRule" in self.option_names:options["Syzygy50MoveRule"]=True
        self.engine.configure(options)
        self.syzygy_path=path;self.syzygy_active=bool(path);self.review_cache.clear()

    @pyqtSlot(str)
    def configure_syzygy(self,path):
        try:
            self._apply_syzygy_path(path)
            self.syzygy_status.emit(path,"",self.syzygy_active)
        except Exception as exc:
            self.syzygy_status.emit(path,str(exc),False)

    @pyqtSlot(int)
    def set_elo(self, value: int):
        self._elo = max(MIN_TARGET_ELO, min(MAX_ELO, int(value)))
        try:
            if self.engine:
                options={}
                if "UCI_Elo" in self.option_names:
                    options["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
                if "Skill Level" in self.option_names:
                    if self._elo < MIN_ELO:
                        skill=0
                    else:
                        skill=round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                    skill_option=self.engine.options["Skill Level"]
                    options["Skill Level"]=max(int(skill_option.min or 0),min(int(skill_option.max or 20),skill))
                if options:self.engine.configure(options)
        except Exception as exc: self.operation_error.emit(str(exc))

    @pyqtSlot(object)
    def play_bot(self, task):
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            version, board = task
            duration = .35 if self._elo < 2000 else .65
            if self._elo < MIN_ELO:
                softness=(MIN_ELO-self._elo)/(MIN_ELO-MIN_TARGET_ELO)
                duration=max(.08,.14+.16*(1-softness))
                multipv_count=min(self._multipv_max,2+round(softness*6))
                move=None
                if "MultiPV" in self.option_names:
                    candidates=self.engine.analyse(board,chess.engine.Limit(time=duration),multipv=multipv_count)
                    if isinstance(candidates,dict):candidates=[candidates]
                    moves=[]
                    for candidate in candidates:
                        pv=candidate.get("pv",[])
                        if pv and pv[0] in board.legal_moves and pv[0] not in moves:moves.append(pv[0])
                    non_best_chance=.10+.78*softness
                    if moves and len(moves)>1 and random.random()<non_best_chance:
                        weaker=moves[1:]
                        weights=[1/(index+1)**1.35 for index in range(len(weaker))]
                        move=random.choices(weaker,weights=weights,k=1)[0]
                    elif moves:
                        move=moves[0]
                if move is None:
                    move=self.engine.play(board,chess.engine.Limit(time=duration)).move
                    if "MultiPV" not in self.option_names and random.random()<.03+.16*softness:
                        legal=list(board.legal_moves)
                        if legal:move=random.choice(legal)
            else:
                move=self.engine.play(board, chess.engine.Limit(time=duration)).move
            self.bot_move.emit((version, board.fen(), move))
        except Exception as exc: self.operation_error.emit(f"Bot move failed: {exc}")

    @pyqtSlot(object)
    def find_best(self, board: chess.Board):
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            info = self.engine.analyse(board, chess.engine.Limit(time=.8))
            pv = info.get("pv", [])
            self.best_move.emit(pv[0] if pv else None)
        except Exception as exc: self.operation_error.emit(f"Best move failed: {exc}")

    @pyqtSlot(object)
    def evaluate_position(self, board: chess.Board):
        try:
            if self.engine:
                info = self.engine.analyse(board, chess.engine.Limit(time=.12))
                self.position_eval.emit(eval_cp(info["score"]))
        except Exception as exc: self.operation_error.emit(f"Position evaluation failed: {exc}")

    @pyqtSlot(object)
    def review_game(self, task):
        review_restore={}
        def restore_review_strength():
            if review_restore and self.engine:
                options=review_restore.copy();review_restore.clear()
                try:self.engine.configure(options)
                except Exception as exc:self.operation_error.emit(f"Could not restore opponent strength after review: {exc}")
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            token,start_board,moves,book_path,budget=task
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:
                full_strength["UCI_LimitStrength"]=False;review_restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:
                review_restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "Skill Level" in self.option_names:
                skill=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                option=self.engine.options["Skill Level"]
                review_restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill))
                full_strength["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),20))
            if full_strength:self.engine.configure(full_strength)
            board = start_board.copy(stack=False)
            results = []
            total = len(moves)
            reader=chess.polyglot.open_reader(book_path) if book_path else None
            def analyze(position,multipv=1):
                key=(position.fen(),tuple(move.uci() for move in position.move_stack),round(float(budget),3),
                     multipv,"full-strength",normalized_syzygy_path(self.syzygy_path) if self.syzygy_active else "")
                if key in self.review_cache:
                    self.review_cache.move_to_end(key)
                    return self.review_cache[key]
                kwargs={"multipv":multipv} if multipv>1 and "MultiPV" in self.option_names else {}
                info=self.engine.analyse(position,chess.engine.Limit(time=budget),**kwargs)
                self.review_cache[key]=info
                while len(self.review_cache)>self.review_cache_limit:self.review_cache.popitem(last=False)
                return info

            piece_ids={square:square for square in start_board.piece_map()}
            piece_move_counts={}
            for i, move in enumerate(moves):
                if self.stop_event.is_set():
                    if reader:reader.close()
                    restore_review_strength()
                    return
                if self.review_cancel_event.is_set():
                    if reader:reader.close()
                    restore_review_strength()
                    self.review_done.emit(token,results,True)
                    return
                before = board.copy()
                if move not in board.legal_moves: raise ValueError(f"Illegal move at ply {i + 1}")
                san = board.san(move)
                legal_count=board.legal_moves.count()
                moving_piece=board.piece_at(move.from_square)
                moving_id=piece_ids.get(move.from_square)
                repeated_piece_moves=piece_move_counts.get(moving_id,0)+1 if moving_id is not None else 1
                base_info = analyze(board)
                base_primary=base_info[0] if isinstance(base_info,list) else base_info
                cp_before = eval_cp(base_primary["score"])
                mate_before=base_primary["score"].white().mate()
                pv_moves = base_info.get("pv", [])
                best = pv_moves[0] if pv_moves and pv_moves[0] in board.legal_moves else move
                base_lines=base_info if isinstance(base_info,list) else [base_info]
                best_pv_moves=tuple(pv_moves[:12])
                best_san = board.san(best)
                best_pv_board = board.copy(stack=False);best_pv_sans=[]
                for pv_move in best_pv_moves:
                    if pv_move not in best_pv_board.legal_moves:break
                    best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                mat_before = material_balance(board)
                board.push(move)
                after_info = analyze(board)
                after_primary=after_info[0] if isinstance(after_info,list) else after_info
                cp_after = eval_cp(after_primary["score"])
                mate_after=after_primary["score"].white().mate()
                loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,mate_before,mate_after)
                before_ep=expected_points(cp_before);actual_ep=expected_points(cp_after)
                before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                verdict, color = classify(loss,ep_loss)
                if move==best:verdict,color="Best","#39a982"
                elif verdict=="Best":verdict,color="Excellent","#4baf73"
                delta = material_balance(board) - mat_before
                side_delta=delta if before.turn==chess.WHITE else -delta
                after_lines=after_info if isinstance(after_info,list) else [after_info]
                response_pv=after_lines[0].get("pv",[]) if after_lines else []
                response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                response_san=board.san(response) if response else ""
                response_board=board.copy(stack=False);response_sans=[]
                for response_move in response_pv[:8]:
                    if response_move not in response_board.legal_moves:break
                    response_sans.append(response_board.san(response_move));response_board.push(response_move)

                # Ask for alternatives only on meaningful misses. This keeps
                # MultiPV focused while surfacing candidate ideas on errors.
                candidate_lines=base_lines
                if best!=move and ep_loss>=.05 and "MultiPV" in self.option_names and self._multipv_max>=2:
                    candidate_info=analyze(before,min(3,self._multipv_max))
                    candidate_lines=candidate_info if isinstance(candidate_info,list) else [candidate_info]
                    if candidate_lines and candidate_lines[0].get("pv"):
                        candidate_best=candidate_lines[0]["pv"][0]
                        if candidate_best in before.legal_moves:
                            best=candidate_best;best_san=before.san(best)
                            best_pv_moves=tuple(candidate_lines[0].get("pv",[])[:12])
                            best_pv_board=before.copy(stack=False);best_pv_sans=[]
                            for pv_move in best_pv_moves:
                                if pv_move not in best_pv_board.legal_moves:break
                                best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)

                best_mover_ep=before_mover_ep
                best_cp=cp_before;best_mate_white=mate_before
                if candidate_lines:
                    line_score=candidate_lines[0].get("score")
                    if line_score is not None:
                        candidate_cp=eval_cp(line_score)
                        best_cp=candidate_cp;best_mate_white=line_score.white().mate()
                        candidate_ep=expected_points(candidate_cp)
                        best_mover_ep=candidate_ep if before.turn==chess.WHITE else 1-candidate_ep
                # Grade the played line against the current best line before
                # deciding whether this position needs a deeper confirmation.
                loss,ep_loss=normalized_move_loss(best_cp,cp_after,before.turn,
                                                 best_mate_white,mate_after)
                verdict,color=classify(loss,ep_loss)
                if move==best:verdict,color="Best","#39a982"
                elif verdict=="Best":verdict,color="Excellent","#4baf73"
                critical_confirmed=False;critical_before_info=None;critical_after_info=None
                candidate_mate=(candidate_lines[0]["score"].pov(before.turn).mate()
                                if candidate_lines and candidate_lines[0].get("score") else None)
                actual_mate=after_primary["score"].pov(before.turn).mate() if after_primary.get("score") else None
                needs_critical_confirmation=(ep_loss>=.10 or candidate_mate is not None or actual_mate is not None)
                if budget>=.18 and needs_critical_confirmation:
                    critical_limit=chess.engine.Limit(time=max(.8,min(1.5,budget*2)))
                    critical_kwargs=({"multipv":min(3,self._multipv_max)}
                                     if "MultiPV" in self.option_names and self._multipv_max>=2 else {})
                    try:
                        critical_before_info=self.engine.analyse(before,critical_limit,**critical_kwargs)
                        critical_after_info=self.engine.analyse(board,critical_limit)
                    except Exception:
                        critical_before_info=None;critical_after_info=None
                    if critical_before_info and critical_after_info:
                        critical_lines=(critical_before_info if isinstance(critical_before_info,list)
                                        else [critical_before_info])
                        critical_best_line=next((line for line in critical_lines
                                                 if line.get("pv") and line["pv"][0] in before.legal_moves
                                                 and line.get("score") is not None),None)
                        critical_after_score=critical_after_info.get("score")
                        if critical_best_line and critical_after_score is not None:
                            candidate_lines=critical_lines
                            best=critical_best_line["pv"][0];best_san=before.san(best)
                            best_pv_moves=tuple(critical_best_line["pv"][:12])
                            best_pv_board=before.copy(stack=False);best_pv_sans=[]
                            for pv_move in best_pv_moves:
                                if pv_move not in best_pv_board.legal_moves:break
                                best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                            cp_before=eval_cp(critical_best_line["score"])
                            mate_before=critical_best_line["score"].white().mate()
                            cp_after=eval_cp(critical_after_score)
                            mate_after=critical_after_score.white().mate()
                            before_ep=expected_points(cp_before);actual_ep=expected_points(cp_after)
                            before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                            actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                            best_cp=cp_before;best_mate_white=mate_before;best_mover_ep=before_mover_ep
                            loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,
                                                               mate_before,mate_after)
                            verdict,color=classify(loss,ep_loss)
                            if move==best:verdict,color="Best","#39a982"
                            elif verdict=="Best":verdict,color="Excellent","#4baf73"
                            after_info=critical_after_info;after_primary=critical_after_info
                            after_lines=[after_primary]
                            response_pv=after_primary.get("pv",[])
                            response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                            response_san=board.san(response) if response else ""
                            response_board=board.copy(stack=False);response_sans=[]
                            for response_move in response_pv[:8]:
                                if response_move not in response_board.legal_moves:break
                                response_sans.append(response_board.san(response_move));response_board.push(response_move)
                            critical_confirmed=True
                turnaround_candidate=(ep_loss<=.02 and best_mover_ep<.25
                                      and actual_mover_ep>=.50)
                best_mate_distance=(candidate_lines[0]["score"].pov(before.turn).mate()
                                    if candidate_lines and candidate_lines[0].get("score") else None)
                best_mate=best_mate_distance is not None and best_mate_distance>0
                actual_mate_distance=(after_primary["score"].pov(before.turn).mate()
                                      if after_primary.get("score") else None)
                if board.is_checkmate():actual_mate_distance=0
                # The played line's distance is measured after one ply has
                # already elapsed, unlike the best line's root distance.
                mate_is_delayed=(best_mate_distance is not None and actual_mate_distance is not None
                                 and actual_mate_distance>0
                                 and actual_mate_distance+1>best_mate_distance)
                missed_mate=(best_mate and (actual_mate_distance is None or actual_mate_distance<0
                                             or mate_is_delayed))
                if best!=move and (missed_mate or (best_mover_ep>=.75 and actual_mover_ep<.55
                                                 and best_mover_ep-actual_mover_ep>=.15)):
                    verdict,color="Miss","#e68a47"

                book_entries=list(reader.find_all(before)) if reader else []
                book_entry=next((entry for entry in book_entries if entry.move==move),None)
                book_status=(f"Book move · weight {book_entry.weight}" if book_entry else "Out of book") if reader else "Book not loaded"
                only_good_gap=0.0
                if (move==best and ep_loss<=.02 and budget>=.4 and not critical_confirmed and not book_entry
                        and legal_count>1 and legal_count<=self._multipv_max
                        and "MultiPV" in self.option_names):
                    all_move_lines=analyze(before,legal_count)
                    all_move_lines=all_move_lines if isinstance(all_move_lines,list) else [all_move_lines]
                    if best in near_best_moves(before,all_move_lines,before.turn):
                        candidate_lines=all_move_lines
                    only_good_gap=only_good_move_gap(before,all_move_lines,before.turn,move,
                                                      legal_count,before_mover_ep)
                    if only_good_gap:verdict,color="Great","#45b9d0"
                verdict,color=override_review_category(verdict,color,legal_count,bool(book_entry))

                # A Great turnaround changes the story of the game, so verify
                # the swing with a deeper search before assigning the label.
                turnaround_confirmed=False;turnaround_before_ep=None;turnaround_after_ep=None
                if (turnaround_candidate and verdict not in ("Book","Forced")
                        and not only_good_gap):
                    confirmation_limit=chess.engine.Limit(time=max(1.2,min(3.0,budget*5)))
                    try:
                        turnaround_before_info=self.engine.analyse(before,confirmation_limit)
                        turnaround_after_info=self.engine.analyse(board,confirmation_limit)
                    except Exception:
                        turnaround_before_info=None;turnaround_after_info=None
                    if turnaround_before_info and turnaround_after_info:
                        before_score=turnaround_before_info.get("score")
                        after_score=turnaround_after_info.get("score")
                        if before_score is not None and after_score is not None:
                            before_deep_white_ep=expected_points(eval_cp(before_score))
                            turnaround_before_ep=(before_deep_white_ep if before.turn==chess.WHITE
                                                  else 1-before_deep_white_ep)
                            if board.is_checkmate():turnaround_after_ep=1.0
                            else:
                                after_deep_white_ep=expected_points(eval_cp(after_score))
                                turnaround_after_ep=(after_deep_white_ep if before.turn==chess.WHITE
                                                     else 1-after_deep_white_ep)
                            turnaround_confirmed=(turnaround_before_ep<.25
                                                  and turnaround_after_ep>=.50
                                                  and turnaround_before_ep-turnaround_after_ep<=.02)
                    if turnaround_confirmed:verdict,color="Great","#45b9d0"

                # Use the regular pass to find candidates, then spend a deeper
                # search only on possible brilliancies. Confirm the actual
                # post-sacrifice position as well as the root alternatives.
                justified=is_engine_justified_sacrifice(before,move,best,best_pv_moves,before_mover_ep,actual_mover_ep,best_mate)
                brilliant_gap=0.0
                if (justified and budget>=.4 and not book_entry and legal_count>1
                        and "MultiPV" in self.option_names and self._multipv_max>=2):
                    confirmation_limit=chess.engine.Limit(time=max(2.0,budget))
                    try:
                        brilliant_info=self.engine.analyse(before,confirmation_limit,
                                                           multipv=min(3,self._multipv_max))
                        brilliant_lines=brilliant_info if isinstance(brilliant_info,list) else [brilliant_info]
                        deep_after_info=None if board.is_checkmate() else self.engine.analyse(board,confirmation_limit)
                    except Exception:
                        # A failed confirmation must never create a Brilliant
                        # label or abort an otherwise usable game review.
                        justified=False;brilliant_gap=0.0
                    else:
                        deep_best_line=next((line for line in brilliant_lines
                                             if line.get("pv") and line["pv"][0] in before.legal_moves),None)
                        deep_actual_mover_ep=1.0 if board.is_checkmate() else None
                        if deep_after_info:
                            deep_actual_white_ep=expected_points(eval_cp(deep_after_info["score"]))
                            deep_actual_mover_ep=(deep_actual_white_ep if before.turn==chess.WHITE
                                                  else 1-deep_actual_white_ep)
                        if deep_best_line and deep_best_line.get("score") and deep_actual_mover_ep is not None:
                            deep_best_move=deep_best_line["pv"][0]
                            deep_best_pv=tuple(deep_best_line["pv"][:12])
                            deep_white_ep=expected_points(eval_cp(deep_best_line["score"]))
                            deep_best_mover_ep=deep_white_ep if before.turn==chess.WHITE else 1-deep_white_ep
                            deep_mate=engine_line_forces_mate(deep_best_line["score"],before.turn)
                            justified=is_engine_justified_sacrifice(before,move,deep_best_move,deep_best_pv,
                                                                     deep_best_mover_ep,deep_actual_mover_ep,deep_mate)
                            if justified:
                                brilliant_gap=brilliant_choice_gap(
                                    before,brilliant_lines,before.turn,move,
                                    minimum_gap=BRILLIANT_MIN_UNIQUENESS_GAP)
                        else:justified=False
                if can_label_brilliant(justified,budget,bool(book_entry),legal_count,
                                       brilliant_gap>=BRILLIANT_MIN_UNIQUENESS_GAP):
                    verdict,color="Brilliant","#b68ae0"
                    # Keep every surface of a Brilliant review on the same
                    # deeper search that earned the label. Otherwise the badge
                    # could cite a confirmed line while the score, best move,
                    # and replay line still came from the shallower pass.
                    candidate_lines=brilliant_lines
                    best=move;best_san=san;best_pv_moves=deep_best_pv
                    best_pv_board=before.copy(stack=False);best_pv_sans=[]
                    for pv_move in best_pv_moves:
                        if pv_move not in best_pv_board.legal_moves:break
                        best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                    cp_before=eval_cp(deep_best_line["score"])
                    mate_before=deep_best_line["score"].white().mate()
                    if deep_after_info:
                        cp_after=eval_cp(deep_after_info["score"])
                        mate_after=deep_after_info["score"].white().mate()
                        actual_ep=expected_points(cp_after)
                    else:
                        # A checkmating sacrifice has no post-move engine
                        # search; record the terminal result directly.
                        cp_after=100_000 if before.turn==chess.WHITE else -100_000
                        mate_after=0
                        actual_ep=1.0 if before.turn==chess.WHITE else 0.0
                    before_ep=expected_points(cp_before)
                    before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                    actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                    best_cp=cp_before;best_mate_white=mate_before
                    best_mover_ep=before_mover_ep
                    loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,
                                                       mate_before,mate_after)
                    best_mate_distance=deep_best_line["score"].pov(before.turn).mate()
                    best_mate=best_mate_distance is not None and best_mate_distance>0
                    actual_mate_distance=(deep_after_info["score"].pov(before.turn).mate()
                                          if deep_after_info else 0)
                    if board.is_checkmate():actual_mate_distance=0
                    mate_is_delayed=(best_mate_distance is not None and actual_mate_distance is not None
                                     and actual_mate_distance>0
                                     and actual_mate_distance+1>best_mate_distance)
                    missed_mate=(best_mate and (actual_mate_distance is None or actual_mate_distance<0
                                                 or mate_is_delayed))
                    after_lines=[deep_after_info] if deep_after_info else []
                    response_pv=deep_after_info.get("pv",[]) if deep_after_info else []
                    response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                    response_san=board.san(response) if response else ""
                    response_board=board.copy(stack=False);response_sans=[]
                    for response_move in response_pv[:8]:
                        if response_move not in response_board.legal_moves:break
                        response_sans.append(response_board.san(response_move));response_board.push(response_move)

                if best!=move and ep_loss>=.05 and verdict not in ("Miss","Book","Forced") and "MultiPV" in self.option_names:
                    # The multi-PV is the same root position, so a large gap
                    # between best and played alternatives strengthens a miss label.
                    scores=[]
                    for line in candidate_lines:
                        line_pv=line.get("pv",[]);line_score=line.get("score")
                        if line_pv and line_score and line_pv[0]==move:
                            score_cp=eval_cp(line_score)
                            scores.append(score_cp if before.turn==chess.WHITE else -score_cp)
                    if scores and best_mover_ep-actual_mover_ep>=.30 and before_mover_ep-best_mover_ep>=.15:
                        verdict,color="Miss","#e68a47"

                side="White" if before.turn==chess.WHITE else "Black"
                events=analyze_review_events(before,move,board,best,response,best_pv_moves,repeated_piece_moves,
                                             tuple(response_pv[:8]),explain_best_difference=ep_loss>=.05 or missed_mate)
                if turnaround_confirmed:
                    events+=(ReviewEvent("turnaround",
                                         f"A deeper engine search confirms this move shifts {side} from "
                                         f"{turnaround_before_ep*100:.0f}% to {turnaround_after_ep*100:.0f}% expected score.",
                                         "positive",
                                         "Which feature of this position lets this move turn a likely loss into a drawable game?",
                                         (move.from_square,move.to_square)),)
                if only_good_gap:
                    events+=(ReviewEvent("only_good_move",
                                         f"{san} was the only legal move that kept at least an equal position; "
                                         f"the next-best legal move loses {only_good_gap*100:.1f} percentage points of expected score.",
                                         "positive",
                                         "Which part of this position makes the other legal moves fail to hold equality?",
                                         (move.from_square,move.to_square)),)
                if missed_mate and best!=move:
                    if actual_mate_distance is not None and actual_mate_distance>0:
                        mate_text=(f"The best move {best_san} forces mate after {best_mate_distance} plies; "
                                   f"{san} still forces mate, but only after {actual_mate_distance+1} plies from here.")
                        mate_prompt=(f"Both moves retain mate. What forcing line after {best_san} ends it sooner than {san}?")
                    else:
                        mate_text=f"The best move {best_san} forces mate after {best_mate_distance} plies; {san} lets the forced mate escape."
                        mate_prompt=f"What sequence of checks makes {best_san} a forced mate after {best_mate_distance} plies?"
                    if any(event.kind=="missed_mate" for event in events):
                        events=tuple(ReviewEvent(event.kind,mate_text,event.severity,mate_prompt,event.focus_squares)
                                     if event.kind=="missed_mate" else event for event in events)
                    else:events+=(ReviewEvent("missed_mate",mate_text,"critical",mate_prompt),)
                elif best_mate and best!=move:
                    events=tuple(event for event in events if event.kind!="missed_mate")
                if verdict=="Brilliant":
                    sacrifice_square=chess.square_name(move.to_square)
                    victim,victim_square=captured_piece_details(before,move)
                    victim_location=(f" on {chess.square_name(victim_square)}" if victim_square is not None else "")
                    offer=(f"takes the {piece_label(victim)}{victim_location} and offers the {piece_label(moving_piece)} on {sacrifice_square}"
                           if victim else f"offers the {piece_label(moving_piece)} on {sacrifice_square}")
                    deep_line_board=before.copy(stack=False);deep_line_sans=[]
                    for line_move in deep_best_pv[:8]:
                        if line_move not in deep_line_board.legal_moves:break
                        deep_line_sans.append(deep_line_board.san(line_move));deep_line_board.push(line_move)
                    line_text=(f" The confirmed line is {' '.join(deep_line_sans)}."
                               if deep_line_sans else "")
                    events+=(ReviewEvent("sound_sacrifice",
                                         f"Your {piece_label(moving_piece)} {san} {offer}; the deeper engine line keeps your expected score within 2 percentage points of, or above, its pre-move level, and MultiPV puts it {brilliant_gap*100:.1f} percentage points ahead of the next-best alternative.{line_text}",
                                         "positive"),)
                actual_themes=tactical_themes(before,move)
                theme=", ".join(sorted(actual_themes))
                best_themes=tactical_themes(before,best) if best!=move else set()
                side_name="White" if before.turn==chess.WHITE else "Black"
                best_reason=(f"The engine estimates {best_mover_ep*100:.0f}% expected score for {side_name} after {best_san}, "
                             f"versus {actual_mover_ep*100:.0f}% after {san}. Line evaluations: "
                             f"{eval_text(best_cp)} after {best_san}, {eval_text(cp_after)} after {san}.")
                if best_themes:
                    best_reason+=" The alternative also creates "+", ".join(sorted(best_themes))+"."
                note=generate_review_commentary(verdict,san,best_san,cp_before,cp_after,loss,events,
                                                response_san,
                                                best_reason," ".join(best_pv_sans),ep_loss,i+1)
                confidence=("Deep confirmation" if critical_confirmed or turnaround_confirmed or verdict=="Brilliant"
                           else "Quick estimate" if budget<.18
                           else "Standard analysis" if budget<.8 else "Thorough analysis")
                if turnaround_confirmed:
                    depth_infos=(turnaround_before_info,turnaround_after_info)
                elif verdict=="Brilliant" and deep_best_line:
                    depth_infos=(deep_best_line,deep_after_info)
                elif critical_confirmed:
                    depth_infos=(critical_best_line,critical_after_info)
                else:
                    depth_infos=(base_primary,after_primary)
                reported_depths=[int(info["depth"]) for info in depth_infos
                                if isinstance(info,dict) and info.get("depth") is not None]
                if reported_depths:
                    confidence+=f" · depth {'/'.join(map(str,reported_depths))}"
                if self.syzygy_active and any(int(info.get("tbhits",0) or 0)>0
                                              for info in depth_infos if isinstance(info,dict)):
                    confidence+=" · Syzygy tablebase hit"
                item = ReviewMove(i + 1, san, best_san, verdict, color, loss, cp_after,
                                  " ".join(best_pv_sans), note, side, book_status,
                                  int(book_entry.weight) if book_entry else 0,
                                  game_phase(before), move_accuracy(loss,ep_loss), best,ep_loss,confidence,theme,
                                  cp_before,response_san,
                                  " ".join(response_sans),best_pv_moves,events,best_reason,mate_before,mate_after,response,
                                  near_best_moves(before,candidate_lines,before.turn),
                                  tuple(before.san(candidate) for candidate in near_best_moves(before,candidate_lines,before.turn) if candidate!=best),
                                  before.fullmove_number,tuple(response_pv[:8]))
                results.append(item)
                if moving_id is not None:
                    piece_move_counts[moving_id]=repeated_piece_moves
                    piece_ids.pop(move.from_square,None)
                    capture_square=move.to_square
                    if before.is_en_passant(move):capture_square=move.to_square-8 if before.turn==chess.WHITE else move.to_square+8
                    piece_ids.pop(capture_square,None)
                    if before.is_castling(move):
                        rank=0 if before.turn==chess.WHITE else 7
                        rook_from=chess.square(7 if move.to_square>move.from_square else 0,rank)
                        rook_to=chess.square(5 if move.to_square>move.from_square else 3,rank)
                        rook_id=piece_ids.pop(rook_from,None)
                        if rook_id is not None:piece_ids[rook_to]=rook_id
                    piece_ids[move.to_square]=moving_id
                self.review_progress.emit(token, i + 1, total, item)
            if reader:reader.close()
            restore_review_strength()
            self.review_done.emit(token, results, False)
        except Exception as exc:
            restore_review_strength()
            if "reader" in locals() and reader:
                try:reader.close()
                except Exception:pass
            if "token" in locals():self.review_done.emit(token,locals().get("results",[]),False)
            self.operation_error.emit(f"Game review failed: {exc}")

    @pyqtSlot(object)
    def analyze_practice_attempt(self,task):
        token,before,attempt,best_move,budget=task;restore={}
        try:
            if not self.engine:raise RuntimeError("Stockfish is not ready")
            if attempt not in before.legal_moves or best_move not in before.legal_moves:
                raise ValueError("The practice position changed before analysis")
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:
                full_strength["UCI_LimitStrength"]=False;restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:
                option=self.engine.options["UCI_Elo"]
                full_strength["UCI_Elo"]=int(option.max or self._uci_elo_max)
                restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "Skill Level" in self.option_names:
                option=self.engine.options["Skill Level"]
                full_strength["Skill Level"]=int(option.max or 20)
                skill=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill))
            if full_strength:self.engine.configure(full_strength)
            mover=before.turn;sign=1 if mover==chess.WHITE else -1
            best_after=before.copy(stack=False);best_after.push(best_move)
            attempt_after=before.copy(stack=False);attempt_after.push(attempt)
            limit=chess.engine.Limit(time=max(.6,min(1.5,float(budget)*2)))

            def evaluate(position):
                if position.is_checkmate():return None,1.0 if not position.turn==mover else 0.0
                if position.is_stalemate() or position.is_insufficient_material():return None,.5
                info=self.engine.analyse(position,limit)
                score=info["score"];cp=eval_cp(score)
                white_ep=expected_points(cp)
                mover_ep=white_ep if mover==chess.WHITE else 1-white_ep
                return (info,score,cp),mover_ep

            best_data,best_ep=evaluate(best_after)
            attempt_data,attempt_ep=evaluate(attempt_after)
            best_score=best_data[1] if best_data else None
            attempt_info,attempt_score,attempt_cp=attempt_data if attempt_data else (None,None,0)
            best_mate_distance=(0 if best_after.is_checkmate() else
                                best_score.pov(mover).mate() if best_score else None)
            attempt_mate_distance=(0 if attempt_after.is_checkmate() else
                                   attempt_score.pov(mover).mate() if attempt_score else None)
            before_cp=best_data[2] if best_data else (100000 if best_ep>=1 else -100000 if best_ep<=0 else 0)
            cp_loss=max(0,(before_cp-attempt_cp)*sign)
            ep_loss=max(0.0,best_ep-attempt_ep)
            verdict,color=classify(cp_loss,ep_loss)
            missed_mate=bool(best_mate_distance is not None and best_mate_distance>=0
                             and (attempt_mate_distance is None or attempt_mate_distance<0
                                  or attempt_mate_distance>best_mate_distance))
            if missed_mate:verdict,color="Miss","#e68a47"
            acceptable=ep_loss<=NEAR_BEST_EXPECTED_POINTS_TOLERANCE and not missed_mate
            pv=tuple(attempt_info.get("pv",[])) if attempt_info else ()
            response=pv[0] if pv and pv[0] in attempt_after.legal_moves else None
            response_san=attempt_after.san(response) if response else ""
            events=analyze_review_events(before,attempt,attempt_after,best_move,response,(),0,pv)
            if missed_mate:
                best_mate_phrase=("is checkmate" if best_after.is_checkmate()
                                   else f"forces mate after {best_mate_distance} plies from the resulting position")
                if attempt_mate_distance is not None and attempt_mate_distance>0:
                    mate_text=(f"The top move {before.san(best_move)} {best_mate_phrase}; "
                               f"your move still forces mate, but only after {attempt_mate_distance} plies from the resulting position.")
                else:
                    mate_text=f"The top move {before.san(best_move)} {best_mate_phrase}; your move lets the forced mate escape."
                if any(event.kind=="missed_mate" for event in events):
                    events=tuple(ReviewEvent(event.kind,mate_text,event.severity,event.prompt,event.focus_squares)
                                 if event.kind=="missed_mate" else event for event in events)
                else:events+=(ReviewEvent("missed_mate",mate_text,"critical"),)
            elif best_mate_distance is not None and best_mate_distance>0:
                events=tuple(event for event in events if event.kind!="missed_mate")
            concrete=[event.text for event in events if event.severity in ("critical","warning") and event.kind!="opponent_check"]
            mover_name="White" if mover==chess.WHITE else "Black"
            comparison=(f"Engine estimate for {mover_name}: {attempt_ep*100:.0f}% expected score after {before.san(attempt)}, "
                        f"compared with {best_ep*100:.0f}% after {before.san(best_move)}.")
            explanation=(" ".join(concrete[:2])+" "+comparison if concrete else comparison)
            reply_text=f" Strongest reply: {response_san}." if response_san else ""
            pv_board=attempt_after.copy(stack=False);pv_sans=[]
            for line_move in pv[:6]:
                if line_move not in pv_board.legal_moves:break
                pv_sans.append(pv_board.san(line_move));pv_board.push(line_move)
            line_text=" Engine line: "+" ".join(pv_sans) if pv_sans else ""
            feedback=(f"Strong choice: this move is within {NEAR_BEST_EXPECTED_POINTS_TOLERANCE*100:.0f} percentage point of the engine's top line. {explanation}"
                      if acceptable else f"{verdict}: {explanation}")
            result={"token":token,"feedback":f"{feedback}{reply_text}{line_text}","acceptable":acceptable}
        except Exception as exc:
            result={"token":token,"feedback":f"Could not analyze that attempt: {exc or 'Stockfish could not evaluate this position.'}"}
        finally:
            if restore and self.engine:
                try:self.engine.configure(restore)
                except Exception:pass
        self.practice_analysis_done.emit(result)

    @pyqtSlot(object)
    def explore(self,task):
        try:
            if not self.engine:raise RuntimeError("Stockfish has not started")
            board,book_path,count=task
            found={}
            if book_path:
                with chess.polyglot.open_reader(book_path) as reader:
                    for entry in reader.find_all(board):
                        if entry.move in board.legal_moves:
                            found[entry.move]={"move":entry.move,"san":board.san(entry.move),"weight":int(entry.weight),"eval":None,"pv":""}
            multipv=min(max(1,count),self._multipv_max)
            info=self.engine.analyse(board,chess.engine.Limit(time=.45),multipv=multipv) if "MultiPV" in self.option_names else self.engine.analyse(board,chess.engine.Limit(time=.65))
            if isinstance(info,dict):info=[info]
            for line in info:
                pv=line.get("pv",[])
                if not pv or pv[0] not in board.legal_moves:continue
                move=pv[0]
                item=found.setdefault(move,{"move":move,"san":board.san(move),"weight":0,"eval":None,"pv":""})
                item["eval"]=eval_cp(line["score"])
                pv_board=board.copy();sans=[]
                for pv_move in pv[:8]:
                    if pv_move not in pv_board.legal_moves:break
                    sans.append(pv_board.san(pv_move));pv_board.push(pv_move)
                item["pv"]=" ".join(sans)
            rows=list(found.values())
            if book_path:rows.sort(key=lambda x:(x["weight"],abs(x["eval"] or 0)),reverse=True)
            else:rows.sort(key=lambda x:(x["eval"] if board.turn==chess.WHITE else -(x["eval"] or 0)),reverse=True)
            self.explore_done.emit((board.fen(),rows))
        except Exception as exc:self.operation_error.emit(f"Opening explorer failed: {exc}")

    @pyqtSlot(object)
    def generate_puzzle(self,task):
        token,skill,difficulty,source_fens,challenge_mode,recent_themes=task
        try:
            if not self.engine:raise RuntimeError("Stockfish is not available")
            difficulty=max(1,min(10,int(difficulty)))
            candidates=[];seen=set()
            for fen in source_fens:
                try:board=chess.Board(fen)
                except (ValueError,TypeError):continue
                key=position_key(board)
                if key not in seen and not board.is_game_over() and len(board.piece_map())>=8:
                    seen.add(key);candidates.append((board,"one of your games"))
            random_count=(16+difficulty*2) if challenge_mode else (8+difficulty*2)
            for _ in range(random_count):
                board=chess.Board();target_plies=random.randint(18,52+difficulty*2)
                for _ply in range(target_plies):
                    if board.is_game_over():break
                    legal=list(board.legal_moves)
                    if not legal:break
                    board.push(random.choice(legal))
                key=position_key(board)
                if key not in seen and not board.is_game_over() and len(board.piece_map())>=8:
                    seen.add(key);candidates.append((board,"a generated legal game position"))
            random.shuffle(candidates)
            attempts=min(len(candidates),(16+difficulty*2) if challenge_mode else (14+difficulty*2))
            if challenge_mode:
                candidates=[item for item in candidates if item[1]=="a generated legal game position"]
                attempts=min(len(candidates),16+difficulty*2)
            else:
                generated=[item for item in candidates if item[1]=="a generated legal game position"]
                saved=[item for item in candidates if item[1]=="one of your games"]
                generated_count=min(len(generated),max(4,attempts//2))
                chosen=random.sample(generated,generated_count) if generated_count else []
                saved_count=min(len(saved),attempts-len(chosen))
                if saved_count:chosen.extend(random.sample(saved,saved_count))
                random.shuffle(chosen);candidates=chosen;attempts=len(candidates)
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:full_strength["UCI_LimitStrength"]=False
            if "Skill Level" in self.option_names:full_strength["Skill Level"]=20
            restore={}
            if "UCI_LimitStrength" in self.option_names:restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "Skill Level" in self.option_names:
                skill_level=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                option=self.engine.options["Skill Level"]
                restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill_level))
            if full_strength:self.engine.configure(full_strength)
            puzzle=None;best_match=None;challenge_pool=[];target_gap=max(12,120-10*(difficulty-1));budget=.10+.025*difficulty
            try:
                for index,(board,source_label) in enumerate(candidates[:attempts],1):
                    if self.stop_event.is_set():break
                    multipv=min(3,self._multipv_max)
                    info=self.engine.analyse(board,chess.engine.Limit(time=budget),multipv=multipv) if "MultiPV" in self.option_names else self.engine.analyse(board,chess.engine.Limit(time=budget))
                    lines=info if isinstance(info,list) else [info]
                    lines=[line for line in lines if line.get("pv") and line["pv"][0] in board.legal_moves and line.get("score")]
                    self.puzzle_progress.emit(token,index,attempts)
                    if not lines:continue
                    best_line=lines[0];best_move=best_line["pv"][0];themes=tactical_themes(board,best_move)
                    if best_line["score"].pov(board.turn).mate() is not None:themes.add("Mate")
                    if (skill!="Any tactic" and skill not in themes) or (challenge_mode and not themes):continue
                    best_score=best_line["score"].pov(board.turn).score(mate_score=10000)
                    if best_score is None:continue
                    second_score=lines[1]["score"].pov(board.turn).score(mate_score=10000) if len(lines)>1 else best_score-100
                    if second_score is None:second_score=best_score-100
                    gap=best_score-second_score
                    if best_score is not None and best_score<-350 and "Mate" not in themes:continue
                    pv=best_line["pv"][:9];pv_board=board.copy();pv_sans=[]
                    for pv_move in pv:
                        if pv_move not in pv_board.legal_moves:break
                        pv_sans.append(pv_board.san(pv_move));pv_board.push(pv_move)
                    theme_order=("Mate","Fork","Pin","Skewer","Discovered attack","Promotion","Check","Winning material")
                    available_themes=[name for name in theme_order if name in themes]
                    fresh_themes=[name for name in available_themes if name not in recent_themes]
                    theme=skill if skill!="Any tactic" else next(iter(fresh_themes or available_themes),"Tactic")
                    candidate_puzzle={"fen":board.fen(),"move":best_move.uci(),"san":board.san(best_move),"theme":theme,
                                      "themes":sorted(themes),"pv":pv_sans,"difficulty":difficulty,"gap":gap,"source":source_label,
                                      "difficulty_match":abs(gap-target_gap)<=40}
                    candidate_puzzle["difficulty_distance"]=abs(gap-target_gap)
                    if best_match is None or candidate_puzzle["difficulty_distance"]<best_match["difficulty_distance"]:best_match=candidate_puzzle
                    if candidate_puzzle["difficulty_match"]:
                        if challenge_mode:challenge_pool.append(candidate_puzzle)
                        else:puzzle=candidate_puzzle;break
            finally:
                if restore:self.engine.configure(restore)
            if challenge_pool:
                varied=[item for item in challenge_pool if item["theme"] not in recent_themes]
                pool=varied or challenge_pool
                best_distance=min(item["difficulty_distance"] for item in pool)
                closest=[item for item in pool if item["difficulty_distance"]<=best_distance+15]
                puzzle=random.choice(closest)
            if puzzle is None:puzzle=best_match
            self.puzzle_done.emit(token,puzzle,"" if puzzle else "No matching position found this time. Try another difficulty, import or save more games, or choose Any tactic.")
        except Exception as exc:
            self.puzzle_done.emit(token,None,f"Puzzle generation failed: {exc}")

    @pyqtSlot()
    def shutdown(self):
        if self.engine:
            try: self.engine.quit()
            except Exception:
                try: self.engine.close()
                except Exception: pass
            self.engine = None
        QThread.currentThread().quit()


class EvalBar(QWidget):
    def __init__(self):
        super().__init__()
        self.cp = 0
        self.setMinimumWidth(44)
        self.setMaximumWidth(54)
    def set_eval(self, cp): self.cp = cp; self.update()
    def paintEvent(self, _event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect(); p.fillRect(r, QColor("#262b2f"))
        bounded = max(-900, min(900, self.cp)); white_share = .5 + bounded / 1800
        white_h = int(r.height() * white_share)
        p.fillRect(0, r.height() - white_h, r.width(), white_h, QColor("#e5e3dc"))
        p.setPen(QColor("#f4f5f6") if white_share < .5 else QColor("#273039"))
        p.setFont(QFont("Arial", 8, QFont.Weight.Bold)); p.drawText(r.adjusted(0, 5, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, eval_text(self.cp))


class ChessBoard(QWidget):
    square_clicked = pyqtSignal(int)
    default_theme = "classic_green"
    default_piece_style = "tournament"
    default_show_coordinates = True
    default_animations = True
    def __init__(self):
        super().__init__()
        self.board = chess.Board(); self.last_move = None; self.selected = None; self.best_move = None;self.review_sticker=None;self.sticker_color="#4baf73";self.focus_squares=()
        self.theme_key=ChessBoard.default_theme
        self.piece_style=ChessBoard.default_piece_style
        self.show_coordinates=ChessBoard.default_show_coordinates
        self.animations_enabled=ChessBoard.default_animations
        self.flipped = False
        self._flash_ticks=0
        self._flash_timer=QTimer(self);self._flash_timer.setInterval(16);self._flash_timer.timeout.connect(self._advance_motion)
        self.setMinimumSize(500, 500); self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    def set_position(self, board, last_move=None, selected=None, best_move=None,review_sticker=None,sticker_color="#4baf73",focus_squares=()):
        changed_move=last_move is not None and last_move!=self.last_move
        self.board, self.last_move, self.selected, self.best_move = board, last_move, selected, best_move
        self.review_sticker,self.sticker_color=review_sticker,sticker_color
        self.focus_squares=tuple(dict.fromkeys(square for square in focus_squares if square is not None))
        if changed_move and self.animations_enabled:
            self._flash_ticks=10;self._flash_timer.start()
        self.update()
    def set_theme(self,theme_key):
        if theme_key in BOARD_THEMES:self.theme_key=theme_key;self.update()
    def set_presentation(self,piece_style=None,show_coordinates=None,animations=None):
        if piece_style in PIECE_STYLES:self.piece_style=piece_style
        if show_coordinates is not None:self.show_coordinates=bool(show_coordinates)
        if animations is not None:
            self.animations_enabled=bool(animations)
            if not self.animations_enabled:self._flash_timer.stop();self._flash_ticks=0
        self.update()
    def _advance_motion(self):
        self._flash_ticks-=1
        if self._flash_ticks<=0:self._flash_ticks=0;self._flash_timer.stop()
        self.update()
    def _sq_rect(self, square, size):
        f, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (7-f, rank) if self.flipped else (f, 7-rank)
        return col*size, row*size, size, size
    def paintEvent(self, _event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()); unit = side/8
        ox, oy = (self.width()-side)/2, (self.height()-side)/2
        theme=BOARD_THEMES.get(self.theme_key,BOARD_THEMES["classic_green"])
        light, dark = QColor(theme["light"]), QColor(theme["dark"])
        p.fillRect(self.rect(),QColor("#16191b"))
        for row in range(8):
            for col in range(8):
                x,y=ox+col*unit,oy+row*unit
                sq = chess.square(7-col if self.flipped else col, row if self.flipped else 7-row)
                base = light if (row+col)%2==0 else dark
                if self.last_move and sq in (self.last_move.from_square, self.last_move.to_square): base = QColor(theme["last"])
                if sq == self.selected: base = QColor(theme["selected"])
                p.fillRect(int(x),int(y),int(unit+1),int(unit+1),base)
                if self._flash_ticks and self.last_move and sq==self.last_move.to_square:
                    flash=QColor("#ffffff");flash.setAlpha(16+self._flash_ticks*9);p.fillRect(int(x),int(y),int(unit+1),int(unit+1),flash)
                if sq in self.focus_squares:
                    p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor("#f1c75b"),max(2,int(unit*.035))))
                    inset=unit*.09;p.drawRoundedRect(QRectF(x+inset,y+inset,unit-2*inset,unit-2*inset),unit*.1,unit*.1)
                if self.best_move and sq in (self.best_move.from_square,self.best_move.to_square):
                    tint=QColor(theme["accent"]);tint.setAlpha(105);p.fillRect(int(x),int(y),int(unit),int(unit),tint)
                piece=self.board.piece_at(sq)
                if piece:
                    piece_style=PIECE_STYLES.get(self.piece_style,PIECE_STYLES["tournament"])
                    font_role=piece_style["font"]
                    if font_role=="sans":font_name="Segoe UI" if sys.platform=="win32" else "DejaVu Sans"
                    else:font_name="Segoe UI Symbol" if sys.platform=="win32" else "DejaVu Sans"
                    weight=QFont.Weight.Bold if piece_style.get("weight")=="bold" else QFont.Weight.Normal
                    p.setFont(QFont(font_name,int(unit*piece_style["scale"]),weight))
                    if piece_style.get("glyph")=="letters":
                        symbol=PIECE_LETTERS[piece.piece_type]
                        if not piece.color:symbol=symbol.lower()
                    else:symbol=GLYPHS[piece.symbol()]
                    if piece_style.get("badge"):
                        badge=QRectF(x+unit*.19,y+unit*.19,unit*.62,unit*.62)
                        badge_color=QColor(theme["dark"] if piece.color else theme["light"])
                        badge_edge=QColor("#f7f6f0") if piece.color else QColor("#202426")
                        p.setBrush(badge_color);p.setPen(QPen(badge_edge,max(1,int(unit*.025))));p.drawEllipse(badge)
                        foreground=badge_edge
                    else:foreground=QColor("#f7f6f0") if piece.color else QColor("#202426")
                    shadow=QColor("#182022") if piece.color else QColor("#dce1dd")
                    shadow.setAlpha(175 if piece_style.get("shadow") else 95)
                    p.setPen(shadow);p.drawText(int(x+unit*.018),int(y+unit*.025),int(unit),int(unit),Qt.AlignmentFlag.AlignCenter,symbol)
                    p.setPen(foreground);p.drawText(int(x),int(y),int(unit),int(unit),Qt.AlignmentFlag.AlignCenter,symbol)
                if self.show_coordinates and col==0:
                    p.setFont(QFont("Arial",max(8,int(unit*.13)),QFont.Weight.Bold))
                    p.setPen(dark if (row+col)%2==0 else light)
                    rank_label=str(1+row) if self.flipped else str(8-row)
                    p.drawText(int(x+3),int(y+2),int(unit*.2),int(unit*.2),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop,rank_label)
                if self.show_coordinates and row==7:
                    p.setFont(QFont("Arial",max(8,int(unit*.13)),QFont.Weight.Bold))
                    p.setPen(dark if (row+col)%2==0 else light)
                    file_label=chr(ord('h')-col) if self.flipped else chr(ord('a')+col)
                    p.drawText(int(x),int(y+unit-unit*.22),int(unit-4),int(unit*.18),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignBottom,file_label)
        if self.selected is not None:
            for move in self.board.legal_moves:
                if move.from_square == self.selected:
                    col,row,w,_=self._sq_rect(move.to_square,unit); center=QColor(theme["accent"]);center.setAlpha(150)
                    p.setBrush(QBrush(center)); p.setPen(Qt.PenStyle.NoPen)
                    if self.board.piece_at(move.to_square): p.drawEllipse(int(ox+col+unit*.08),int(oy+row+unit*.08),int(unit*.84),int(unit*.84))
                    else: p.drawEllipse(int(ox+col+unit*.39),int(oy+row+unit*.39),int(unit*.22),int(unit*.22))
        if self.board.is_check():
            king=self.board.king(self.board.turn)
            if king is not None:
                col,row,w,h=self._sq_rect(king,unit); p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor("#e44848"),4))
                p.drawRect(int(ox+col+2),int(oy+row+2),int(w-4),int(h-4))
        if self.best_move and self.best_move in self.board.legal_moves:
            sx,sy,_,_=self._sq_rect(self.best_move.from_square,unit); tx,ty,_,_=self._sq_rect(self.best_move.to_square,unit)
            sx+=ox+unit/2; sy+=oy+unit/2; tx+=ox+unit/2; ty+=oy+unit/2
            arrow=QColor(theme["accent"]);arrow.setAlpha(220)
            p.setPen(QPen(arrow,max(4,int(unit*.09)),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(int(sx),int(sy),int(tx),int(ty))
            angle=math.atan2(ty-sy,tx-sx);head=max(8,unit*.18)
            p.setBrush(arrow);p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(tx,ty),QPointF(tx-head*math.cos(angle-.5),ty-head*math.sin(angle-.5)),QPointF(tx-head*math.cos(angle+.5),ty-head*math.sin(angle+.5))]))
        if self.review_sticker:
            badge_width=min(side*.36,175);badge=QRectF(ox+side-badge_width-8,oy+8,badge_width,28);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.sticker_color));p.drawRoundedRect(badge,8,8)
            p.setPen(QColor("#ffffff"));p.setFont(QFont("Segoe UI",9,QFont.Weight.Bold));p.drawText(badge,Qt.AlignmentFlag.AlignCenter,self.review_sticker.upper())
    def mousePressEvent(self,event):
        side=min(self.width(),self.height()); unit=side/8; ox=(self.width()-side)/2; oy=(self.height()-side)/2
        col=int((event.position().x()-ox)//unit); row=int((event.position().y()-oy)//unit)
        if 0<=col<8 and 0<=row<8:
            sq=chess.square(7-col if self.flipped else col,row if self.flipped else 7-row)
            self.square_clicked.emit(sq)


class OpeningExplorer(QDialog):
    def __init__(self, board, book_path, request_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opening & Response Explorer"); fit_dialog_to_screen(self, parent, 1600, 1050)
        self.position=board.copy(); self.start=board.copy(); self.line=[]; self.moves=[]
        self.book_path=book_path; self.request_callback=request_callback
        layout=QVBoxLayout(self)
        self.heading=QLabel("Explore candidate moves and replies"); self.heading.setObjectName("PlayerName"); layout.addWidget(self.heading)
        self.path_label=QLabel("Starting position"); self.path_label.setObjectName("Muted"); layout.addWidget(self.path_label)
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();preview_side=max(380,min(560,int(screen.availableGeometry().height()*.52))) if screen else 500
        center=QHBoxLayout(); self.preview=ChessBoard(); self.preview.setMinimumSize(preview_side,preview_side); center.addWidget(self.preview,1)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(["Move","Book weight","Eval (White)","Engine line"])
        self.table.verticalHeader().hide(); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.setAlternatingRowColors(True); self.table.setShowGrid(False)
        header=self.table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_:self.play_selected()); center.addWidget(self.table,2); layout.addLayout(center,1)
        row=QHBoxLayout(); self.hint=QLabel("Book weights are relative move frequencies when the selected book provides them."); self.hint.setObjectName("Muted"); row.addWidget(self.hint,1)
        self.back_btn=QPushButton("Back"); self.back_btn.clicked.connect(self.back); row.addWidget(self.back_btn)
        self.reset_btn=QPushButton("Reset line"); self.reset_btn.clicked.connect(self.reset_line); row.addWidget(self.reset_btn)
        self.play_btn=QPushButton("Play move"); self.play_btn.setObjectName("PrimaryButton"); self.play_btn.clicked.connect(self.play_selected); row.addWidget(self.play_btn)
        layout.addLayout(row)
        self.request_lines()

    def request_lines(self):
        self.heading.setText(f"{('White' if self.position.turn else 'Black')} to move · requesting lines…")
        self.request_callback((self.position.copy(),self.book_path,6))

    def show_lines(self,payload):
        fen,rows=payload
        if fen!=self.position.fen():return
        self.preview.set_position(self.position,self.moves[-1] if self.moves else None)
        self.heading.setText(f"{('White' if self.position.turn else 'Black')} to move · {len(rows)} candidate moves")
        self.path_label.setText("Line: " + (" ".join(self.line) if self.line else "starting position"))
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            values=[row["san"],str(row["weight"]) if row["weight"] else "—",eval_text(row["eval"]) if row["eval"] is not None else "—",row["pv"] or "Book response"]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if col==0:cell.setData(Qt.ItemDataRole.UserRole,row["move"])
                self.table.setItem(i,col,cell)
        if rows:self.table.selectRow(0)
        if self.book_path:
            self.hint.setText("Book weights show relative frequency; engine scores are shown when available.")
        else:
            self.hint.setText("No weighted book loaded. Showing Stockfish variations; load a Polyglot .bin book for book frequencies.")

    def play_selected(self):
        row=self.table.currentRow()
        if row<0:return
        item=self.table.item(row,0); move=item.data(Qt.ItemDataRole.UserRole) if item else None
        if move not in self.position.legal_moves:return
        self.line.append(self.position.san(move)); self.moves.append(move); self.position.push(move); self.request_lines()

    def back(self):
        if not self.line:return
        self.line.pop(); self.moves.pop(); self.position=self.start.copy()
        for move in self.moves:self.position.push(move)
        self.preview.set_position(self.position,self.moves[-1] if self.moves else None)
        self.request_lines()

    def reset_line(self):
        self.position=self.start.copy(); self.line=[]; self.moves=[]; self.preview.set_position(self.position); self.request_lines()


CHESSCOM_API_ROOT="https://api.chess.com/pub"
CHESSCOM_ECO_PATTERN=re.compile(r"^[A-E]\d{2}$",re.IGNORECASE)


def _opening_name_key(value):
    return re.sub(r"[^a-z0-9]+","",str(value or "").casefold())


@lru_cache(maxsize=1)
def chesscom_opening_catalog():
    """Index bundled Lichess opening names by ECO and normalized display name."""
    by_eco={};by_name={}
    for path in discover_lichess_files(BUNDLE_DIR/"openings"):
        try:
            with path.open("r",encoding="utf-8-sig",newline="") as source:
                for row in csv.DictReader(source,delimiter="\t"):
                    code=str(row.get("eco","")).strip().upper()
                    name=str(row.get("name","")).strip()
                    if not CHESSCOM_ECO_PATTERN.fullmatch(code) or not name:continue
                    pgn=str(row.get("pgn","")).strip()
                    try:
                        opening=chess.pgn.read_game(io.StringIO(pgn))
                        plies=len(list(opening.mainline_moves())) if opening else 10_000
                    except (ValueError,TypeError):plies=10_000
                    entry=(plies,name)
                    by_eco.setdefault(code,[]).append(entry)
                    by_name.setdefault(_opening_name_key(name),[]).append((code,name))
        except (OSError,UnicodeError,csv.Error):
            continue
    # ECO-only imports are ambiguous, so prefer the name assigned at the
    # shortest move sequence; exact Chess.com opening URLs take precedence.
    generic={code:min(entries,key=lambda item:(item[0],len(item[1]),item[1]))[1]
             for code,entries in by_eco.items()}
    return generic,by_name


def chesscom_opening_label(opening="",eco_code="",eco_url=""):
    """Return a readable opening name with its ECO code when one is known."""
    source=str(opening or "").strip()
    code=str(eco_code or "").strip().upper()
    url=str(eco_url or "").strip()
    if source.startswith("http") and not url:
        url=source;source=""
    # Accept labels already saved by newer builds, while normalizing old ECO-only rows.
    existing=re.match(r"^(.*?)\s*[·|]\s*([A-E]\d{2})$",source,re.IGNORECASE)
    if existing:
        source=existing.group(1).strip();code=code or existing.group(2).upper()
    if CHESSCOM_ECO_PATTERN.fullmatch(source):
        code=code or source.upper();source=""
    if not CHESSCOM_ECO_PATTERN.fullmatch(code):code=""

    catalog,by_name=chesscom_opening_catalog()
    slug=""
    if url.startswith("http"):
        slug=urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    source_slug=source if source and " " not in source and "-" in source else ""
    lookup_key=_opening_name_key(slug or source_slug)
    if source_slug:source=""
    if source.casefold() in ("unknown","unknown opening","opening not found in local database","opening database not loaded"):
        source=""
    if not source and lookup_key:
        matches=by_name.get(lookup_key,[])
        exact=next((name for match_code,name in matches if code and match_code==code),None)
        if exact:source=exact
        elif matches:source=matches[0][1]
        else:source=re.sub(r"[-_]+"," ",slug or source_slug).title()
    if not source and code:source=catalog.get(code,"")
    if source and code:return f"{source} · {code}"
    return source or code or "Unknown opening"


def normalize_chesscom_username(value: str) -> str:
    username=(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,25}",username):
        raise ValueError("Enter a Chess.com username using 2–25 letters, numbers, underscores, or hyphens.")
    return username


def chesscom_game_identifier(game):
    if not isinstance(game,dict):return ""
    identifier=str(game.get("url","")).strip() or str(game.get("uuid","")).strip()
    if identifier:return identifier
    pgn=game.get("pgn")
    return hashlib.sha256(pgn.encode("utf-8")).hexdigest() if isinstance(pgn,str) and pgn else ""


def validate_chesscom_archive_url(value: str) -> str:
    parsed=urllib.parse.urlparse(str(value))
    if (parsed.scheme!="https" or parsed.hostname!="api.chess.com"
            or not re.fullmatch(r"/pub/player/[A-Za-z0-9_-]+/games/\d{4}/(?:0[1-9]|1[0-2])/?",parsed.path)):
        raise ValueError("Chess.com supplied an invalid archive address.")
    return str(value)


def validate_chesscom_country_url(value: str) -> str:
    parsed=urllib.parse.urlparse(str(value))
    if (parsed.scheme!="https" or parsed.hostname!="api.chess.com"
            or not re.fullmatch(r"/pub/country/[A-Za-z]{2}/?",parsed.path)):
        raise ValueError("Chess.com supplied an invalid country address.")
    return str(value)


def chesscom_request_json(url: str) -> dict:
    request=urllib.request.Request(url,headers={
        "User-Agent":"OfflineChess/1.0 (Chess.com public game archive importer)",
        "Accept":"application/json",
        "Accept-Encoding":"gzip",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=25) as response:
                payload=response.read()
                if response.headers.get("Content-Encoding")=="gzip":
                    import gzip
                    payload=gzip.decompress(payload)
            break
        except urllib.error.HTTPError as exc:
            if exc.code==429 and attempt<2:
                try:delay=max(1,min(10,int(exc.headers.get("Retry-After","2"))))
                except (TypeError,ValueError):delay=2
                time.sleep(delay);continue
            if exc.code==404:raise ValueError("Chess.com could not find that username or archive.") from exc
            if exc.code==429:raise ValueError("Chess.com is rate limiting requests. Wait a little, then try again.") from exc
            raise ValueError(f"Chess.com returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError,TimeoutError) as exc:
            raise ValueError(f"Could not reach Chess.com: {exc.reason if hasattr(exc,'reason') else exc}") from exc
    else:raise ValueError("Chess.com remained busy after retrying.")
    try:
        value=json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise ValueError("Chess.com returned data the app could not read.") from exc
    if not isinstance(value,dict):raise ValueError("Chess.com returned an unexpected response.")
    return value


class ChessComFetchWorker(QThread):
    archives_ready=pyqtSignal(object)
    month_ready=pyqtSignal(object)
    month_downloaded=pyqtSignal(str,object)
    progress=pyqtSignal(int,int)
    country_ready=pyqtSignal(str,str)
    countries_progress=pyqtSignal(int,int)
    countries_finished=pyqtSignal(int,int)
    failed=pyqtSignal(str)
    all_finished=pyqtSignal()

    def __init__(self,operation,username,archive_url="",opponents=None,parent=None,
                 known_game_ids=None,minimum_archive_month=""):
        super().__init__(parent);self.operation=operation;self.cancel_event=threading.Event()
        self.username=normalize_chesscom_username(username);self.archive_url=archive_url;self.opponents=list(opponents or [])
        self.known_game_ids=set(known_game_ids or ());self.minimum_archive_month=str(minimum_archive_month or "")
        self.new_games_added=0;self.archives_checked=0

    def cancel(self):self.cancel_event.set()

    def run(self):
        try:
            if self.operation=="archives":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                data=chesscom_request_json(url)
                archives=data.get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                self.archives_ready.emit(archives)
            elif self.operation=="month":
                data=chesscom_request_json(validate_chesscom_archive_url(self.archive_url))
                games=data.get("games",[])
                if not isinstance(games,list):raise ValueError("Chess.com returned an invalid game list.")
                self.month_ready.emit(games)
            elif self.operation=="all":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                archives=chesscom_request_json(url).get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                self.progress.emit(0,len(archives))
                for index,archive in enumerate(archives,1):
                    if self.cancel_event.is_set():break
                    data=chesscom_request_json(validate_chesscom_archive_url(archive))
                    games=data.get("games",[])
                    if not isinstance(games,list):raise ValueError(f"Chess.com returned an invalid archive: {archive}")
                    self.month_downloaded.emit(str(archive),games)
                    self.progress.emit(index,len(archives))
                self.all_finished.emit()
            elif self.operation=="update":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                archives=chesscom_request_json(url).get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                relevant=[]
                for archive in archives:
                    match=re.search(r"/(\d{4})/(\d{2})/?$",str(archive))
                    if match and match.group(1)+"-"+match.group(2)>=self.minimum_archive_month:
                        relevant.append((match.group(1)+"-"+match.group(2),str(archive)))
                relevant.sort()
                self.progress.emit(0,len(relevant))
                seen=set(self.known_game_ids)
                for index,(_month,archive) in enumerate(relevant,1):
                    if self.cancel_event.is_set():break
                    data=chesscom_request_json(validate_chesscom_archive_url(archive))
                    games=data.get("games",[])
                    if not isinstance(games,list):raise ValueError(f"Chess.com returned an invalid archive: {archive}")
                    new_games=[]
                    for game in games:
                        game_id=chesscom_game_identifier(game)
                        if game_id and game_id not in seen:
                            seen.add(game_id);new_games.append(game)
                    if new_games:
                        self.new_games_added+=len(new_games)
                        self.month_downloaded.emit(archive,new_games)
                    self.archives_checked=index
                    self.progress.emit(index,len(relevant))
                self.all_finished.emit()
            elif self.operation=="countries":
                country_names={};completed=0;total=len(self.opponents)
                for index,raw_opponent in enumerate(self.opponents,1):
                    if self.cancel_event.is_set():break
                    opponent=normalize_chesscom_username(raw_opponent)
                    profile_url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(opponent)}"
                    try:
                        profile=chesscom_request_json(profile_url)
                        country_url=validate_chesscom_country_url(profile.get("country",""))
                        if country_url not in country_names:
                            country=chesscom_request_json(country_url)
                            country_names[country_url]=str(country.get("name") or country.get("code") or "")
                        country_name=country_names[country_url]
                    except ValueError as exc:
                        if "could not find that username" in str(exc).casefold():country_name=""
                        elif "invalid country address" in str(exc).casefold():country_name=""
                        else:raise
                    self.country_ready.emit(opponent,country_name)
                    completed=index;self.countries_progress.emit(completed,total)
                self.countries_finished.emit(completed,total)
            else:raise ValueError("Unknown Chess.com download operation.")
        except Exception as exc:self.failed.emit(str(exc))


class ChessComCastlingScanWorker(QThread):
    progress=pyqtSignal(int,int)
    scan_done=pyqtSignal(object)

    def __init__(self,records,parent=None):
        super().__init__(parent);self.records=list(records)

    def run(self):
        results=[];total=len(self.records)
        for index,record in enumerate(self.records,1):
            if self.isInterruptionRequested():break
            game_id=record.get("game_id");phase="Did not castle";castle_ply=None;error=""
            try:
                path=Path(record.get("pgn_path") or "")
                with path.open("r",encoding="utf-8-sig") as source:
                    game=chess.pgn.read_game(source)
                if game is None:raise ValueError("PGN contains no game")
                board=game.board()
                for ply,move in enumerate(game.mainline_moves(),1):
                    if board.is_castling(move):
                        phase=game_phase(board);castle_ply=ply;break
                    board.push(move)
            except (OSError,UnicodeError,ValueError,chess.InvalidMoveError,chess.IllegalMoveError) as exc:
                error=str(exc) or "Could not read PGN"
            results.append((game_id,phase,castle_ply,error))
            self.progress.emit(index,total)
        self.scan_done.emit(results)


class ProfileStore:
    """Small local summary database; full PGNs are optional and off by default."""
    def __init__(self):
        if os.name=="nt":
            base=Path(os.environ.get("LOCALAPPDATA",str(Path.home()/"AppData"/"Local")))
        else:
            base=Path(os.environ.get("XDG_DATA_HOME",str(Path.home()/".local"/"share")))
        self.path=base/"OfflineChess"/"profile.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=self.connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS games(
                game_key TEXT PRIMARY KEY, played_at TEXT NOT NULL, result TEXT NOT NULL,
                user_color TEXT NOT NULL, user_result REAL, opponent_elo INTEGER,
                elo_approx INTEGER NOT NULL, user_accuracy REAL,
                white_accuracy REAL, black_accuracy REAL,
                plies INTEGER NOT NULL, opening TEXT NOT NULL, average_loss REAL,
                pgn TEXT, stats_json TEXT NOT NULL DEFAULT '{}', review_json TEXT)""")
            columns={row[1] for row in db.execute("PRAGMA table_info(games)")}
            if "stats_json" not in columns:db.execute("ALTER TABLE games ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")
            if "review_json" not in columns:db.execute("ALTER TABLE games ADD COLUMN review_json TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('store_full_pgns','0')")
            db.execute("""CREATE TABLE IF NOT EXISTS chesscom_games(
                game_id TEXT PRIMARY KEY, username TEXT NOT NULL, played_at TEXT NOT NULL,
                result TEXT NOT NULL, user_color TEXT NOT NULL, user_result REAL,
                player_rating INTEGER, opponent TEXT NOT NULL, opponent_rating INTEGER,
                time_control TEXT NOT NULL, time_class TEXT NOT NULL, rated INTEGER NOT NULL,
                rules TEXT NOT NULL, opening TEXT NOT NULL, pgn_path TEXT NOT NULL,
                reviewed INTEGER NOT NULL DEFAULT 0, user_accuracy REAL, chesscom_accuracy REAL, predicted_elo INTEGER,
                review_json TEXT NOT NULL DEFAULT '[]', stats_json TEXT NOT NULL DEFAULT '{}',
                opponent_country TEXT NOT NULL DEFAULT '', opponent_country_checked INTEGER NOT NULL DEFAULT 0,
                castling_scanned INTEGER NOT NULL DEFAULT 0, castling_phase TEXT NOT NULL DEFAULT '', castling_ply INTEGER)""")
            imported_columns={row[1] for row in db.execute("PRAGMA table_info(chesscom_games)")}
            if "stats_json" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")
            if "opponent_country" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN opponent_country TEXT NOT NULL DEFAULT ''")
            if "opponent_country_checked" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN opponent_country_checked INTEGER NOT NULL DEFAULT 0")
            if "chesscom_accuracy" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN chesscom_accuracy REAL")
            if "castling_scanned" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_scanned INTEGER NOT NULL DEFAULT 0")
            if "castling_phase" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_phase TEXT NOT NULL DEFAULT ''")
            if "castling_ply" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_ply INTEGER")
            db.commit()
        finally:db.close()
        self._migrate_saved_accuracy_model()

    def connect(self):
        return sqlite3.connect(self.path,timeout=10)

    @staticmethod
    def _recalibrate_saved_accuracy(user_color,user_accuracy,white_accuracy,black_accuracy,
                                    stats_source,review_source):
        try:stats=json.loads(stats_source or "{}")
        except (TypeError,ValueError):stats={}
        try:review=json.loads(review_source) if review_source else None
        except (TypeError,ValueError):review=None
        moves=review.get("moves") if isinstance(review,dict) else None
        if isinstance(moves,list):
            by_side={"White":[],"Black":[]};by_phase={}
            valid_moves=0
            for move in moves:
                if not isinstance(move,dict):continue
                try:
                    raw=raw_move_accuracy(int(move.get("loss",0)),move.get("expected_loss"))
                    move["accuracy"]=calibrate_accuracy(raw);valid_moves+=1
                except (TypeError,ValueError):continue
                side=move.get("side")
                if side in by_side:by_side[side].append(raw)
                phase=move.get("phase")
                if side==user_color and phase in ("Opening","Middlegame","Endgame"):
                    by_phase.setdefault(phase,[]).append(raw)
            if valid_moves:
                aggregate=lambda values:calibrate_accuracy(sum(values)/len(values)) if values else None
                white_accuracy=aggregate(by_side["White"]) if by_side["White"] else white_accuracy
                black_accuracy=aggregate(by_side["Black"]) if by_side["Black"] else black_accuracy
                recalibrated_user_accuracy=aggregate(by_side.get(user_color,[]))
                if recalibrated_user_accuracy is not None:user_accuracy=recalibrated_user_accuracy
                if isinstance(stats,dict):
                    stats["phase_accuracy"]={phase:aggregate(values) for phase,values in by_phase.items()}
                return (user_accuracy,white_accuracy,black_accuracy,
                        json.dumps(stats,separators=(",",":")),
                        json.dumps(review,separators=(",",":")))
        def recalibrate(value):
            try:return calibrate_accuracy(float(value)) if value is not None else None
            except (TypeError,ValueError):return value
        if isinstance(stats,dict) and isinstance(stats.get("phase_accuracy"),dict):
            stats["phase_accuracy"]={phase:recalibrate(value)
                                     for phase,value in stats["phase_accuracy"].items()}
        return (recalibrate(user_accuracy),recalibrate(white_accuracy),recalibrate(black_accuracy),
                json.dumps(stats,separators=(",",":")) if isinstance(stats,dict) else stats_source,
                review_source)

    def _migrate_saved_accuracy_model(self):
        db=self.connect()
        try:
            saved=db.execute("SELECT value FROM settings WHERE key='accuracy_model_version'").fetchone()
            if saved and saved[0]==ACCURACY_MODEL_VERSION:return
            local_rows=db.execute("SELECT game_key,user_color,user_accuracy,white_accuracy,black_accuracy,stats_json,review_json FROM games").fetchall()
            local_updates=[]
            for key,user_color,user_accuracy,white_accuracy,black_accuracy,stats_source,review_source in local_rows:
                values=self._recalibrate_saved_accuracy(user_color,user_accuracy,white_accuracy,black_accuracy,
                                                         stats_source,review_source)
                local_updates.append((*values,key))
            db.executemany("UPDATE games SET user_accuracy=?,white_accuracy=?,black_accuracy=?,stats_json=?,review_json=? WHERE game_key=?",local_updates)
            chesscom_rows=db.execute("SELECT game_id,user_color,user_accuracy,stats_json,review_json FROM chesscom_games").fetchall()
            chesscom_updates=[]
            for game_id,user_color,user_accuracy,stats_source,review_source in chesscom_rows:
                user_accuracy,_,_,stats_source,review_source=self._recalibrate_saved_accuracy(
                    user_color,user_accuracy,None,None,stats_source,review_source)
                chesscom_updates.append((user_accuracy,stats_source,review_source,game_id))
            db.executemany("UPDATE chesscom_games SET user_accuracy=?,stats_json=?,review_json=? WHERE game_id=?",chesscom_updates)
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('accuracy_model_version',?)",
                       (ACCURACY_MODEL_VERSION,))
            db.commit()
        finally:db.close()

    def chesscom_root(self,username):
        safe=normalize_chesscom_username(username)
        return self.path.parent/"chesscom_games"/safe.lower()

    def save_chesscom_game(self,username,game):
        username=normalize_chesscom_username(username)
        if not isinstance(game,dict) or not isinstance(game.get("pgn"),str) or not game["pgn"].strip():
            raise ValueError("The selected archive entry has no PGN to save.")
        white=game.get("white") if isinstance(game.get("white"),dict) else {}
        black=game.get("black") if isinstance(game.get("black"),dict) else {}
        if str(white.get("username","")).casefold()==username.casefold():user_color="White";player=white;opponent_data=black
        elif str(black.get("username","")).casefold()==username.casefold():user_color="Black";player=black;opponent_data=white
        else:raise ValueError("This game does not list the selected account as a player.")
        game_id=chesscom_game_identifier(game)
        if not game_id:raise ValueError("The selected archive entry has no game ID.")
        stable_id=hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:16]
        end_time=game.get("end_time")
        try:played_at=datetime.fromtimestamp(int(end_time),datetime.now().astimezone().tzinfo).isoformat(timespec="minutes")
        except (TypeError,ValueError,OverflowError,OSError):
            try:played_at=chess.pgn.read_game(io.StringIO(game["pgn"])).headers.get("Date","????.??.??").replace(".","-")
            except Exception:played_at="Unknown date"
        white_result=game.get("white",{}).get("result","") if isinstance(game.get("white"),dict) else ""
        black_result=game.get("black",{}).get("result","") if isinstance(game.get("black"),dict) else ""
        pgn_game=chess.pgn.read_game(io.StringIO(game["pgn"]))
        if pgn_game is None:raise ValueError("The downloaded PGN could not be parsed.")
        board=pgn_game.board()
        for move in pgn_game.mainline_moves():
            if move not in board.legal_moves:raise ValueError("The downloaded PGN contains an illegal move.")
            board.push(move)
        result=pgn_game.headers.get("Result",game.get("result","*"))
        user_result=(1.0 if result==("1-0" if user_color=="White" else "0-1") else
                     .5 if result=="1/2-1/2" else 0.0 if result in ("1-0","0-1") else None)
        root=self.chesscom_root(username)
        try:month=datetime.fromisoformat(played_at).strftime("%Y-%m")
        except ValueError:month="unknown-date"
        folder=root/month;folder.mkdir(parents=True,exist_ok=True)
        opponent=str(opponent_data.get("username","Unknown"))
        filename=f"{played_at[:10]}_{re.sub(r'[^A-Za-z0-9_-]+','_',opponent)[:24]}_{stable_id}.pgn"
        target=folder/filename;temporary=target.with_suffix(".pgn.tmp")
        temporary.write_text(game["pgn"].rstrip()+"\n",encoding="utf-8")
        temporary.replace(target)
        def rating(person):
            try:return int(person.get("rating"))
            except (TypeError,ValueError):return None
        accuracies=game.get("accuracies") if isinstance(game.get("accuracies"),dict) else {}
        try:
            chesscom_accuracy=float(accuracies.get(user_color.lower()))
            if not math.isfinite(chesscom_accuracy) or not 0<=chesscom_accuracy<=100:chesscom_accuracy=None
        except (TypeError,ValueError):chesscom_accuracy=None
        opening_url=game.get("eco") or pgn_game.headers.get("ECOUrl","")
        opening=chesscom_opening_label(pgn_game.headers.get("Opening",""),
                                       pgn_game.headers.get("ECO",""),opening_url)
        country=str(opponent_data.get("country","")).strip()
        if country.startswith("http"):country=""
        record=(game_id,username,played_at,result,user_color,user_result,rating(player),opponent,rating(opponent_data),
                str(game.get("time_control","")),str(game.get("time_class","")),int(bool(game.get("rated",False))),
                str(game.get("rules","chess")),str(opening),str(target),0,None,chesscom_accuracy,None,"[]",country,int(bool(country)))
        db=self.connect()
        try:
            db.execute("""INSERT INTO chesscom_games(game_id,username,played_at,result,user_color,user_result,
                player_rating,opponent,opponent_rating,time_control,time_class,rated,rules,opening,pgn_path,
                reviewed,user_accuracy,chesscom_accuracy,predicted_elo,review_json,opponent_country,opponent_country_checked)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id) DO UPDATE SET username=excluded.username,played_at=excluded.played_at,
                result=excluded.result,user_color=excluded.user_color,user_result=excluded.user_result,
                player_rating=excluded.player_rating,opponent=excluded.opponent,opponent_rating=excluded.opponent_rating,
                time_control=excluded.time_control,time_class=excluded.time_class,rated=excluded.rated,
                rules=excluded.rules,opening=excluded.opening,pgn_path=excluded.pgn_path,
                chesscom_accuracy=COALESCE(excluded.chesscom_accuracy,chesscom_games.chesscom_accuracy)""",record)
            db.commit()
        finally:db.close()
        return game_id

    def chesscom_games(self,username):
        username=normalize_chesscom_username(username);db=self.connect()
        try:
            rows=db.execute("""SELECT game_id,username,played_at,result,user_color,user_result,player_rating,opponent,
                opponent_rating,time_control,time_class,rated,rules,opening,pgn_path,reviewed,user_accuracy,
                predicted_elo,review_json,stats_json,opponent_country,opponent_country_checked,chesscom_accuracy,
                castling_scanned,castling_phase,castling_ply
                FROM chesscom_games WHERE username=? COLLATE NOCASE ORDER BY played_at DESC,game_id""",
                (username,)).fetchall()
        finally:db.close()
        columns=("game_id","username","played_at","result","user_color","user_result","player_rating","opponent",
                 "opponent_rating","time_control","time_class","rated","rules","opening","pgn_path","reviewed",
                 "user_accuracy","predicted_elo","review_json","stats_json","opponent_country","opponent_country_checked",
                 "chesscom_accuracy","castling_scanned","castling_phase","castling_ply")
        records=[dict(zip(columns,row)) for row in rows]
        for record in records:
            record["opening"]=chesscom_opening_label(record.get("opening",""))
        return records

    def set_chesscom_opponent_country(self,username,opponent,country):
        username=normalize_chesscom_username(username);opponent=normalize_chesscom_username(opponent)
        db=self.connect()
        try:
            cursor=db.execute("""UPDATE chesscom_games SET opponent_country=?,opponent_country_checked=1
                WHERE username=? COLLATE NOCASE AND opponent=? COLLATE NOCASE""",
                (str(country or "").strip(),username,opponent))
            db.commit();return cursor.rowcount
        finally:db.close()

    def remove_chesscom_game(self,game_id):
        db=self.connect()
        try:row=db.execute("SELECT pgn_path FROM chesscom_games WHERE game_id=?",(game_id,)).fetchone()
        finally:db.close()
        if row:
            try:Path(row[0]).unlink(missing_ok=True)
            except OSError:pass
            db=self.connect()
            try:db.execute("DELETE FROM chesscom_games WHERE game_id=?",(game_id,));db.commit()
            finally:db.close()

    def set_chesscom_review(self,game_id,reviewed,user_accuracy=None,predicted_elo=None,review_json="[]",stats_json="{}"):
        db=self.connect()
        try:
            db.execute("UPDATE chesscom_games SET reviewed=?,user_accuracy=?,predicted_elo=?,review_json=?,stats_json=? WHERE game_id=?",
                       (int(bool(reviewed)),user_accuracy,predicted_elo,review_json,stats_json,game_id));db.commit()
        finally:db.close()

    def set_chesscom_castling_scans(self,scans):
        rows=[]
        for game_id,phase,ply in scans:
            if phase not in ("Opening","Middlegame","Endgame","Did not castle"):
                raise ValueError("Invalid castling phase")
            rows.append((phase,ply,game_id))
        if not rows:return
        db=self.connect()
        try:
            db.executemany("UPDATE chesscom_games SET castling_scanned=1,castling_phase=?,castling_ply=? WHERE game_id=?",rows);db.commit()
        finally:db.close()

    def full_pgns_enabled(self):
        db=self.connect()
        try:
            row=db.execute("SELECT value FROM settings WHERE key='store_full_pgns'").fetchone()
            return bool(row and row[0]=="1")
        finally:db.close()

    def get_setting(self,key,default=None):
        db=self.connect()
        try:
            row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            return row[0] if row else default
        finally:db.close()

    def set_setting(self,key,value):
        db=self.connect()
        try:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
            db.commit()
        finally:db.close()

    def set_full_pgns(self,enabled):
        db=self.connect()
        try:
            db.execute("UPDATE settings SET value=? WHERE key='store_full_pgns'",("1" if enabled else "0",))
            db.commit()
        finally:db.close()

    def save_review(self,record):
        columns=("game_key","played_at","result","user_color","user_result","opponent_elo","elo_approx",
                 "user_accuracy","white_accuracy","black_accuracy","plies","opening","average_loss","pgn","stats_json","review_json")
        updates=[f"{c}=excluded.{c}" for c in columns[1:] if c!="pgn"]
        updates.append("pgn=COALESCE(excluded.pgn,games.pgn)")
        db=self.connect()
        try:
            db.execute(f"INSERT INTO games({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                       "ON CONFLICT(game_key) DO UPDATE SET " + ",".join(updates),
                       tuple(record[c] for c in columns))
            db.commit()
        finally:db.close()

    def games(self):
        db=self.connect()
        try:
            rows=db.execute("SELECT game_key,played_at,result,user_color,user_result,opponent_elo,elo_approx,user_accuracy,white_accuracy,black_accuracy,plies,opening,average_loss,pgn,stats_json,review_json FROM games ORDER BY played_at DESC").fetchall()
        finally:db.close()
        names=("game_key","played_at","result","user_color","user_result","opponent_elo","elo_approx","user_accuracy","white_accuracy","black_accuracy","plies","opening","average_loss","pgn","stats_json","review_json")
        return [dict(zip(names,row)) for row in rows]

    def clear_pgns(self):
        db=self.connect()
        try:
            imported=[row[0] for row in db.execute("SELECT pgn_path FROM chesscom_games WHERE pgn_path!=''")]
            db.execute("UPDATE games SET pgn=NULL,review_json=NULL")
            db.execute("UPDATE chesscom_games SET pgn_path=''");db.commit()
        finally:db.close()
        for path in imported:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass

    def delete_game(self,game_key):
        db=self.connect()
        try:db.execute("DELETE FROM games WHERE game_key=?",(game_key,));db.commit()
        finally:db.close()

    def clear_all(self):
        db=self.connect()
        try:
            imported=[row[0] for row in db.execute("SELECT pgn_path FROM chesscom_games")]
            db.execute("DELETE FROM games");db.execute("DELETE FROM chesscom_games");db.commit()
        finally:db.close()
        for path in imported:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass


def prepare_chesscom_pgn(record):
    """Preserve the source PGN while adding the player's side for local review coaching."""
    pgn_text=Path(record["pgn_path"]).read_text(encoding="utf-8-sig")
    game=chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:raise ValueError("The saved Chess.com PGN could not be read.")
    game.headers["OfflineChessUserColor"]=record["user_color"]
    game.headers["OfflineChessPlayedLocally"]="0"
    try:game.headers["Date"]=datetime.fromisoformat(record["played_at"]).strftime("%Y.%m.%d")
    except (ValueError,TypeError):pass
    return game.accept(chess.pgn.StringExporter(headers=True,variations=True,comments=True))+"\n"


def chesscom_pgn_clock_positions(game):
    """Return the recorded White and Black clocks at each mainline position."""
    positions=[(None,None)];white_clock=black_clock=None;node=game
    for _move in game.mainline_moves():
        node=node.variation(0)
        clock=node.clock()
        if clock is not None:
            if node.parent.board().turn==chess.WHITE:white_clock=clock
            else:black_clock=clock
        positions.append((white_clock,black_clock))
    return positions


def format_chesscom_clock(seconds):
    if seconds is None:return "—:——"
    try:seconds=max(0.0,float(seconds))
    except (TypeError,ValueError):return "—:——"
    whole=int(seconds);fraction=seconds-whole
    if whole>=3600:
        hours,remaining=divmod(whole,3600);minutes,secs=divmod(remaining,60)
        return f"{hours}:{minutes:02d}:{secs:02d}{f'.{int(fraction*10)}' if fraction>=.05 else ''}"
    minutes,secs=divmod(whole,60)
    return f"{minutes}:{secs:02d}{f'.{int(fraction*10)}' if fraction>=.05 else ''}"


class AccuracyChart(QWidget):
    def __init__(self,values=None,parent=None):
        super().__init__(parent); self.values=values or []; self.setMinimumHeight(180);self.setToolTip(MOVE_QUALITY_TOOLTIP)
    def set_values(self,values):self.values=values;self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(42,14,-14,-28);p.setFont(QFont("Arial",9));p.setPen(QColor("#8f999e"))
        for value in (0,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(2,int(y-8),34,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Move quality appears after your first reviewed game.");return
        pts=[];n=len(self.values)
        for i,value in enumerate(self.values):
            x=area.left()+(area.width()*(i/(n-1) if n>1 else .5));bounded=max(0,min(100,float(value)))
            y=area.bottom()-(bounded/100)*area.height();pts.append(QPointF(x,y))
        p.setPen(QPen(QColor("#88c66a"),3));
        for a,b in zip(pts,pts[1:]):p.drawLine(a,b)
        for point in pts:p.setPen(QPen(QColor("#b5e393"),2));p.setBrush(QColor("#82bd60"));p.drawEllipse(point,4,4)


class EloHistoryChart(QWidget):
    def __init__(self,values=None,parent=None):
        super().__init__(parent);self.values=list(values or []);self.setMinimumHeight(170);self.setMouseTracking(True);self.setToolTip("Chess.com rating after each downloaded game, in date order.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(54,14,-16,-28);p.setFont(QFont("Arial",9));p.setPen(QColor("#8f999e"))
        if not self.values:
            p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Download rated games to build your Chess.com rating history.");return
        ratings=[int(item[1]) for item in self.values];low=(min(ratings)//100)*100;high=((max(ratings)+99)//100)*100
        if high<=low:low-=100;high+=100
        else:low=max(0,low-50);high+=50
        for value in range(low,high+1,max(100,math.ceil((high-low)/4/100)*100)):
            y=area.bottom()-(value-low)/(high-low)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),48,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,str(value))
        points=[]
        for index,(_,rating) in enumerate(self.values):
            x=area.left()+area.width()*(index/(len(self.values)-1) if len(self.values)>1 else .5)
            y=area.bottom()-(rating-low)/(high-low)*area.height();points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#80c7db"),2.5))
        for a,b in zip(points,points[1:]):p.drawLine(a,b)
        for point in points:p.setPen(QPen(QColor("#d3f1f7"),1));p.setBrush(QColor("#80c7db"));p.drawEllipse(point,3.5,3.5)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+7,100,18,Qt.AlignmentFlag.AlignLeft, str(self.values[0][0])[:10])
        p.drawText(area.right()-100,area.bottom()+7,100,18,Qt.AlignmentFlag.AlignRight,str(self.values[-1][0])[:10])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(54,14,-16,-28)
        fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        index=round(fraction*(len(self.values)-1));date,rating=self.values[index]
        QToolTip.showText(event.globalPosition().toPoint(),f"{date} · {rating} Chess.com rating",self)
    def leaveEvent(self,event):
        QToolTip.hideText();super().leaveEvent(event)


class ChessComResultsChart(QWidget):
    """Compact monthly results chart for downloaded Chess.com games."""
    COLORS={"wins":QColor("#80c7db"),"draws":QColor("#d6b86a"),"losses":QColor("#df8278")}
    def __init__(self,parent=None):
        super().__init__(parent);self.months=[];self.setMinimumHeight(170);self.setMouseTracking(True)
        self.setToolTip("Monthly win, draw, and loss proportions for the selected time control.")
    def set_values(self,months):self.months=list(months or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,14,-14,-30);p.setFont(QFont("Arial",9))
        if not self.months:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Download completed games to see monthly results.");return
        legend_x=area.left()
        for label,key in (("Wins","wins"),("Draws","draws"),("Losses","losses")):
            p.fillRect(QRectF(legend_x,2,9,9),self.COLORS[key]);p.setPen(QColor("#b3bcc0"));p.drawText(int(legend_x+13),0,48,13,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,label)
            legend_x+=62
        for value in (0,25,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        count=len(self.months);step=area.width()/count;bar_width=max(8,min(34,step*.58))
        for index,item in enumerate(self.months):
            month,wins,draws,losses=item;total=wins+draws+losses
            if not total:continue
            x=area.left()+step*(index+.5);bottom=area.bottom()
            for amount,key in ((losses,"losses"),(draws,"draws"),(wins,"wins")):
                height=area.height()*amount/total
                if height>0:p.fillRect(QRectF(x-bar_width/2,bottom-height,bar_width,height),self.COLORS[key])
                bottom-=height
            p.setPen(QColor("#929ca0"))
            if count<=6 or index in (0,count-1) or index%max(1,count//5)==0:
                p.drawText(int(x-step/2),area.bottom()+7,int(step),18,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,month[2:])
            if count<=12:p.drawText(int(x-step/2),area.top()-1,int(step),14,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,str(total))
    def mouseMoveEvent(self,event):
        if not self.months:return
        area=self.rect().adjusted(48,14,-14,-30);fraction=max(0,min(.999,(event.position().x()-area.left())/max(1,area.width())))
        month,wins,draws,losses=self.months[min(len(self.months)-1,int(fraction*len(self.months)))];total=wins+draws+losses
        score=(wins+.5*draws)/total*100 if total else 0
        QToolTip.showText(event.globalPosition().toPoint(),f"{month} · {wins} wins / {draws} draws / {losses} losses · {score:.0f}% score",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComAccuracyChart(QWidget):
    """Accuracy trend indexed either by month or by the player's move number."""
    def __init__(self,description="accuracy",parent=None):
        super().__init__(parent);self.values=[];self.description=description;self.setMinimumHeight(160);self.setMouseTracking(True)
        source="Chess.com archive" if "Chess.com" in description else "local reviewed games"
        self.setToolTip(f"Average {source} {description}; hover a point for its sample size.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,14,-16,-28);p.setFont(QFont("Arial",9))
        for value in (0,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        if not self.values:
            message=("No Chess.com accuracy data in the downloaded games."
                     if "Chess.com" in self.description else "Current game reviews will build this accuracy trend.")
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,message);return
        points=[];n=len(self.values)
        for index,(_label,value,_count) in enumerate(self.values):
            x=area.left()+area.width()*(index/(n-1) if n>1 else .5);y=area.bottom()-(max(0,min(100,float(value)))/100)*area.height()
            points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#a8d47a"),2.5))
        for a,b in zip(points,points[1:]):p.drawLine(a,b)
        for point in points:p.setBrush(QColor("#a8d47a"));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3.5,3.5)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+6,120,18,Qt.AlignmentFlag.AlignLeft,self.values[0][0])
        p.drawText(area.right()-120,area.bottom()+6,120,18,Qt.AlignmentFlag.AlignRight,self.values[-1][0])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(48,14,-16,-28);fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        label,value,count=self.values[round(fraction*(len(self.values)-1))]
        QToolTip.showText(event.globalPosition().toPoint(),f"{label} · {value:.1f}% average · {count} games",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComShapeChart(QWidget):
    """Average user-side expected-score trace for a selected local game-flow pattern."""
    def __init__(self,parent=None):
        super().__init__(parent);self.profiles={};self.pattern="";self.setMinimumHeight(190);self.setMouseTracking(True)
        self.setToolTip("Average expected score for reviewed games in the selected local flow pattern.")
    def set_profiles(self,profiles):
        self.profiles=dict(profiles or {})
        if self.pattern not in self.profiles:
            self.pattern=max(self.profiles,key=lambda name:self.profiles[name][1],default="")
        self.update()
    def set_pattern(self,pattern):self.pattern=str(pattern or "");self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,18,-18,-32);p.setFont(QFont("Arial",9))
        if not self.pattern or self.pattern not in self.profiles:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Review games to build local game-flow traces.");return
        values,count=self.profiles[self.pattern]
        for score in (0,25,50,75,100):
            y=area.bottom()-(score/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{score}%")
        points=[]
        for index,value in enumerate(values):
            x=area.left()+area.width()*(index/max(1,len(values)-1));y=area.bottom()-(max(0,min(100,float(value)*100))/100)*area.height()
            points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#88c66a"),2.5))
        for left,right in zip(points,points[1:]):p.drawLine(left,right)
        for point in points:p.setBrush(QColor("#88c66a"));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3,3)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+7,100,18,Qt.AlignmentFlag.AlignLeft,"Game start")
        p.drawText(area.center().x()-45,area.bottom()+7,90,18,Qt.AlignmentFlag.AlignHCenter,"Midpoint")
        p.drawText(area.right()-100,area.bottom()+7,100,18,Qt.AlignmentFlag.AlignRight,"Game end")
    def mouseMoveEvent(self,event):
        if not self.pattern or self.pattern not in self.profiles:return
        values,count=self.profiles[self.pattern];area=self.rect().adjusted(48,18,-18,-32)
        fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        index=round(fraction*(len(values)-1));progress=round(fraction*100)
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"{self.pattern} · {progress}% through game · {values[index]*100:.1f}% expected score · {count} games",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComQualityTrendChart(QWidget):
    """Monthly shares of locally reviewed moves by quality band."""
    SERIES=(("Brilliant / Great","#b68ae0"),("Best","#45b9d0"),
            ("Excellent / Good","#8bbf65"),("Errors / Misses","#e68a47"),
            ("Book / Forced","#64b5d6"))
    def __init__(self,parent=None):
        super().__init__(parent);self.values=[];self.setMinimumHeight(190);self.setMouseTracking(True)
        self.setToolTip("Monthly share of your locally reviewed moves in each quality band.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,48,-16,-28);p.setFont(QFont("Arial",9))
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Current game reviews will build this move-quality trend.");return
        cell_width=area.width()/3
        for index,(label,color) in enumerate(self.SERIES):
            legend_x=area.left()+(index%3)*cell_width;legend_y=14+(index//3)*18
            p.setPen(QColor(color));p.setBrush(QColor(color));p.drawEllipse(QPointF(legend_x+4,legend_y),4,4)
            p.setPen(QColor("#b3bcc0"));p.drawText(int(legend_x+12),int(legend_y-8),max(32,int(cell_width-16)),17,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,label)
        for value in (0,25,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        for series_index,(_label,color) in enumerate(self.SERIES):
            points=[]
            for index,(_month,shares,_total) in enumerate(self.values):
                x=area.left()+area.width()*(index/max(1,len(self.values)-1))
                y=area.bottom()-(max(0,min(100,float(shares[series_index])))/100)*area.height()
                points.append(QPointF(x,y))
            p.setPen(QPen(QColor(color),2))
            for left,right in zip(points,points[1:]):p.drawLine(left,right)
            for point in points:p.setBrush(QColor(color));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3,3)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+6,120,18,Qt.AlignmentFlag.AlignLeft,self.values[0][0])
        p.drawText(area.right()-120,area.bottom()+6,120,18,Qt.AlignmentFlag.AlignRight,self.values[-1][0])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(48,48,-16,-28);fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        month,shares,total=self.values[round(fraction*(len(self.values)-1))]
        details=" · ".join(f"{label}: {shares[index]:.1f}%" for index,(label,_color) in enumerate(self.SERIES))
        QToolTip.showText(event.globalPosition().toPoint(),f"{month} · {total:,} reviewed moves · {details}",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


def predicted_performance_elo(games,min_games=8):
    """Estimate result-based playing performance; never present it as an official rating."""
    usable=[]
    for game in games:
        try:
            if not bool(game.get("rated",False)):continue
            rating=int(game["opponent_rating"]);score=float(game["user_result"])
            if 100<=rating<=4000 and 0<=score<=1:usable.append((rating,score))
        except (KeyError,TypeError,ValueError):continue
    if len(usable)<min_games:return None
    mean_opponent=sum(rating for rating,_ in usable)/len(usable)
    target=sum(score for _,score in usable)+.5
    def total_expected(player_rating):
        return sum(1/(1+10**max(-10,min(10,(opponent-player_rating)/400))) for opponent,_ in usable)+1/(1+10**max(-10,min(10,(mean_opponent-player_rating)/400)))
    low,high=100.0,4000.0
    for _ in range(64):
        middle=(low+high)/2
        if total_expected(middle)<target:low=middle
        else:high=middle
    return round((low+high)/2)


def recurring_review_themes(game_stats: list[dict], limit: int = 3) -> list[tuple[str,int]]:
    """Count error themes by distinct reviewed games, not event frequency."""
    games_by_theme={}
    for stats in game_stats:
        themes=stats.get("error_themes",{}) if isinstance(stats,dict) else {}
        if not isinstance(themes,dict):continue
        for name,count in themes.items():
            try:present=int(count)>0
            except (TypeError,ValueError):present=False
            if present:games_by_theme[name]=games_by_theme.get(name,0)+1
    recurring=[(name,count) for name,count in games_by_theme.items() if count>=2]
    return sorted(recurring,key=lambda item:(-item[1],item[0]))[:max(0,limit)]


def recurring_review_strengths(game_stats: list[dict], limit: int = 2) -> list[tuple[str,int]]:
    """Count engine-approved positive themes by distinct reviewed games."""
    games_by_strength={}
    for stats in game_stats:
        strengths=stats.get("strength_themes",{}) if isinstance(stats,dict) else {}
        if not isinstance(strengths,dict):continue
        for name,count in strengths.items():
            try:present=int(count)>0
            except (TypeError,ValueError):present=False
            if present:games_by_strength[name]=games_by_strength.get(name,0)+1
    recurring=[(name,count) for name,count in games_by_strength.items() if count>=2]
    return sorted(recurring,key=lambda item:(-item[1],item[0]))[:max(0,limit)]


class MomentumChart(QWidget):
    move_selected=pyqtSignal(int)
    def __init__(self,parent=None,player_color=None):
        super().__init__(parent);self.player_color=player_color;self.values=[];self.moves=[];self.selected_ply=0;self._last_tooltip="";self.setMouseTracking(True);self.setMinimumHeight(106);self.setMaximumHeight(130);self.setToolTip("Colored dots mark key move classifications. Hover for details; click to jump to a move.")
    def set_values(self,values,selected_ply=0,moves=None):
        self.values=values;self.selected_ply=selected_ply;self.moves=list(moves or ());self._last_tooltip="";self.update()
    def _ply_at_x(self,x):
        area=self.rect().adjusted(42,8,-10,-19)
        fraction=max(0,min(1,(x-area.left())/max(1,area.width())))
        return round(fraction*(len(self.values)-1))+1
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(42,8,-10,-19);p.setFont(QFont("Arial",8))
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Momentum appears after the game review.");return
        mid=area.top()+area.height()/2;p.setPen(QPen(QColor("#41494c"),1,Qt.PenStyle.DashLine));p.drawLine(area.left(),int(mid),area.right(),int(mid))
        p.setPen(QColor("#8f999e"));p.drawText(2,area.top()+9,38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"White +");p.drawText(2,int(mid+5),38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"Even");p.drawText(2,area.bottom(),38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"Black +")
        count=len(self.values);points=[]
        for i,value in enumerate(self.values):
            cp=max(-1000,min(1000,value));x=area.left()+area.width()*(i/(count-1) if count>1 else .5);y=mid-(cp/2000)*area.height()
            points.append(QPointF(x,y))
        for i,(a,b) in enumerate(zip(points,points[1:])):
            avg=(self.values[i]+self.values[i+1])/2
            p.setPen(QPen(QColor("#82bd60" if avg>=0 else "#d17969"),2.4));p.drawLine(a,b)
        marker_colors={"Brilliant":"#b68ae0","Great":"#45b9d0","Miss":"#e68a47",
                       "Inaccuracy":"#e0b746","Mistake":"#e68a47","Blunder":"#e05252"}
        for index,item in enumerate(self.moves[:len(points)]):
            marker=marker_colors.get(item.verdict)
            if marker:
                p.setPen(QPen(QColor("#202426"),1));p.setBrush(QColor(marker));p.drawEllipse(points[index],3.5,3.5)
        if 1<=self.selected_ply<=len(points):
            point=points[self.selected_ply-1];p.setPen(QPen(QColor("#f2cb58"),1,Qt.PenStyle.DashLine));p.drawLine(int(point.x()),area.top(),int(point.x()),area.bottom())
            p.setPen(QPen(QColor("#f2cb58"),1));p.setBrush(QColor("#f2cb58"));p.drawEllipse(point,4,4)
    def mousePressEvent(self,event):
        if not self.values:return
        self.move_selected.emit(self._ply_at_x(event.position().x()))
    def mouseMoveEvent(self,event):
        if not self.values or not self.moves:
            self._last_tooltip="";return
        index=min(self._ply_at_x(event.position().x())-1,len(self.moves)-1)
        item=self.moves[index]
        parts=[f"{review_move_notation(item)} · {item.side}",
               f"Evaluation: {evaluation_label(item.eval_after,item.mate_after)}",
               f"{item.verdict} · {item.expected_loss*100:.1f} pp expected score lost",
               f"Engine choice: {item.best_san}",
               f"Analysis: {item.confidence}"]
        if item.best_san!=item.san and item.best_reason:
            parts.append("Why: "+review_text_for_viewer(item.best_reason,item,self.player_color))
        detail=review_text_for_viewer(item.explanation.strip(),item,self.player_color)
        if detail:parts.append(detail)
        tooltip="\n".join(parts)
        if tooltip!=self._last_tooltip:
            QToolTip.showText(event.globalPosition().toPoint(),tooltip,self)
            self._last_tooltip=tooltip
    def leaveEvent(self,event):
        self._last_tooltip="";QToolTip.hideText();super().leaveEvent(event)


class MistakePracticeDialog(QDialog):
    """Replay review errors and missed chances as retryable positions before revealing the line."""
    analysis_requested=pyqtSignal(object)
    def __init__(self,start_board,moves,history,parent=None,start_ply=None,include_all=False,
                 player_color=chess.WHITE):
        super().__init__(parent);self.setWindowTitle("Practice key positions");fit_dialog_to_screen(self,parent,1200,1050)
        self.start_board=start_board.copy(stack=False);self.history=list(history)
        self.player_color=player_color
        self.board_flipped=player_color==chess.BLACK
        self.items=list(moves) if include_all else [item for item in moves if item.verdict in ("Miss","Inaccuracy","Mistake","Blunder")]
        self.index=next((i for i,item in enumerate(self.items) if item.ply==start_ply),0);self.position=None;self.selected=None;self.revealed=False;self.line_board=None;self.line_moves=[];self.feedback_token=0;self.feedback_pending=False;self.last_attempt=None;self.hint_stage=0
        layout=QVBoxLayout(self);self.heading=QLabel();self.heading.setObjectName("Brand");layout.addWidget(self.heading)
        self.prompt=QLabel("Find a stronger move. Try before revealing the engine suggestion.");self.prompt.setWordWrap(True);self.prompt.setObjectName("ReviewSummary");layout.addWidget(self.prompt)
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();board_side=max(400,min(780,int(screen.availableGeometry().height()*.52))) if screen else 600
        self.board=ChessBoard();self.board.flipped=self.board_flipped;self.board.setMinimumSize(board_side,board_side);self.board.square_clicked.connect(self.on_square);layout.addWidget(self.board,1)
        status_row=QHBoxLayout();self.status=QLabel();self.status.setObjectName("Muted");self.status.setWordWrap(True);status_row.addWidget(self.status,1)
        self.flip_btn=QPushButton("Flip board");self.flip_btn.clicked.connect(self.flip_board)
        self.flip_btn.setToolTip(f"{'Black' if self.board_flipped else 'White'} is at the bottom. Click to flip.")
        status_row.addWidget(self.flip_btn);layout.addLayout(status_row)
        row=QHBoxLayout();self.hint_btn=QPushButton("Hint");self.hint_btn.clicked.connect(self.show_hint);row.addWidget(self.hint_btn)
        self.reveal_btn=QPushButton("Show best move");self.reveal_btn.clicked.connect(self.reveal);row.addWidget(self.reveal_btn)
        self.line_btn=QPushButton("Play best line");self.line_btn.clicked.connect(self.play_line_step);self.line_btn.setEnabled(False);row.addWidget(self.line_btn)
        row.addStretch();self.next_btn=QPushButton("Next position");self.next_btn.setObjectName("PrimaryButton");self.next_btn.clicked.connect(self.next_position);row.addWidget(self.next_btn);done=QPushButton("Close");done.clicked.connect(self.accept);row.addWidget(done);layout.addLayout(row)
        self._load_position()

    def flip_board(self):
        self.board_flipped=not self.board_flipped
        self.board.flipped=self.board_flipped;self.board.update()
        self.flip_btn.setToolTip(f"{'Black' if self.board_flipped else 'White'} is at the bottom. Click to flip.")

    def _load_position(self):
        self.feedback_token+=1;self.feedback_pending=False
        if self.index>=len(self.items):
            self.heading.setText("Practice complete");self.prompt.setText("You’ve revisited every reviewed mistake. You can close this session now.");self.status.setText(f"Completed {count_label(len(self.items),'position')}.");self.board.set_position(self.start_board);self.next_btn.setEnabled(False);self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False);self.line_btn.setEnabled(False);return
        item=self.items[self.index];self.position=self.start_board.copy(stack=False)
        try:
            for move in self.history[:item.ply-1]:self.position.push(move)
        except (ValueError,IndexError):self.position=self.start_board.copy(stack=False)
        self.selected=None;self.revealed=False;self.line_board=None;self.line_moves=[];self.last_attempt=None;self.hint_stage=0
        self.heading.setText(f"Position {self.index+1} of {len(self.items)} · {review_move_notation(item)} · {item.verdict}")
        self.prompt.setText(f"{item.side} to move. The game move was {item.san}. Find the engine’s top move or an equally strong choice before revealing it.")
        self.status.setText("Select one of your pieces, then its destination. Try freely; your move stays local.")
        self.board.set_position(self.position,last_move=None,selected=None,best_move=None,review_sticker=None)
        self.next_btn.setText("Next position" if self.index+1<len(self.items) else "Finish")
        self.line_btn.setEnabled(False);self.reveal_btn.setEnabled(True);self.reveal_btn.setText("Show best move");self.hint_btn.setEnabled(True);self.hint_btn.setText("Hint")

    def on_square(self,square):
        if self.position is None or self.revealed:return
        piece=self.position.piece_at(square)
        if self.selected is None:
            if piece and piece.color==self.position.turn:self.selected=square
        else:
            candidate=chess.Move(self.selected,square)
            item=self.items[self.index]
            if self.position.piece_type_at(self.selected)==chess.PAWN and chess.square_rank(square) in (0,7):
                candidate.promotion=item.best_move.promotion if item.best_move.from_square==self.selected and item.best_move.to_square==square else chess.QUEEN
            if candidate in self.position.legal_moves:
                if candidate in (item.acceptable_moves or (item.best_move,)):
                    before=self.position.copy(stack=False)
                    after=before.copy(stack=False);after.push(candidate)
                    if candidate==item.best_move:
                        response_pv=tuple(getattr(item,"best_pv_moves",())[1:10])
                        response=response_pv[0] if response_pv and response_pv[0] in after.legal_moves else None
                        events=analyze_review_events(before,candidate,after,candidate,response,
                                                     tuple(getattr(item,"best_pv_moves",())[:10]),
                                                     response_pv=response_pv[1:] if response else ())
                        stored=[event for event in getattr(item,"events",())
                                if event.severity=="positive" and event.kind=="sound_sacrifice"]
                        ideas=stored or [event for event in events if event.severity=="positive"]
                    else:ideas=[]
                    self.position.push(candidate);self.revealed=True
                    if candidate==item.best_move:
                        detail=f" {review_text_for_viewer(ideas[0].text,item,self.player_color)}" if ideas else ""
                        self.status.setText(f"That’s the engine’s top move.{detail} Now compare the continuation or move on.");sticker="Top move"
                    else:
                        self.status.setText("Strong choice. The engine rates this move effectively equal to its top line.");sticker="Strong choice"
                    self.board.set_position(self.position,last_move=candidate,selected=None,best_move=None,review_sticker=sticker,sticker_color="#39a982");self.selected=None
                    self.hint_btn.setEnabled(False);self.reveal_btn.setText("Show best line");return
                self.feedback_token+=1;self.feedback_pending=True;self.selected=None
                self.last_attempt=candidate
                self.status.setText("Checking that move against Stockfish…")
                self.board.set_position(self.position,last_move=candidate,selected=None,best_move=None,review_sticker="Analysing",sticker_color="#e0b746")
                self.board.setEnabled(False);self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False);self.next_btn.setEnabled(False)
                budget=next((seconds for label,seconds in (("Quick estimate",.12),
                              ("Standard analysis",.4),("Thorough analysis",.8))
                             if item.confidence.startswith(label)),.24)
                self.analysis_requested.emit((self.feedback_token,self.position.copy(stack=False),candidate,item.best_move,budget))
                return
            self.selected=square if piece and piece.color==self.position.turn else None
        self.board.set_position(self.position,last_move=None,selected=self.selected,best_move=None)

    def apply_engine_feedback(self,result):
        if result.get("token")!=self.feedback_token:return
        self.feedback_pending=False;self.board.setEnabled(True);self.hint_btn.setEnabled(self.hint_stage<2);self.reveal_btn.setEnabled(True);self.next_btn.setEnabled(True)
        item=self.items[self.index]
        self.status.setText(review_text_for_viewer(result.get("feedback","Stockfish could not analyze that attempt."),item,self.player_color))
        if result.get("acceptable") and self.last_attempt in self.position.legal_moves:
            self.position.push(self.last_attempt);self.revealed=True
            self.hint_btn.setEnabled(False);self.reveal_btn.setText("Show best line")
            self.board.set_position(self.position,last_move=self.last_attempt,selected=None,best_move=None,
                                    review_sticker="Strong choice",sticker_color="#39a982")
            return
        self.board.set_position(self.position,last_move=self.last_attempt,selected=None,best_move=None,review_sticker="Try again",sticker_color="#e0b746")

    def show_hint(self):
        item=self.items[self.index]
        if self.hint_stage>=2:return
        self.hint_stage+=1
        clue=review_practice_hint(self.position,item.best_move,item.best_pv_moves,self.hint_stage)
        self.status.setText(f"Hint {self.hint_stage}: {clue} Try to improve on the move that was played.")
        if self.hint_stage==1:self.hint_btn.setText("Next hint")
        else:self.hint_btn.setText("Hints used");self.hint_btn.setEnabled(False)

    def reveal(self):
        item=self.items[self.index];self.revealed=True;self.selected=None
        self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False)
        explanation=review_text_for_viewer(item.explanation,item,self.player_color)
        self.status.setText(f"Best move: {item.best_san}. {explanation} Best line: {item.pv or item.best_san}")
        self.board.set_position(self.position,last_move=None,selected=None,best_move=item.best_move,review_sticker="Best move",sticker_color="#39a982")
        line_board=self.position.copy();self.line_moves=[]
        for san in item.pv.split():
            try:move=line_board.parse_san(san)
            except ValueError:break
            self.line_moves.append(move);line_board.push(move)
        self.line_board=self.position.copy();self.line_btn.setEnabled(bool(self.line_moves));self.line_btn.setText("Play best line" if self.line_moves else "No line available")

    def play_line_step(self):
        if not self.line_moves or self.line_board is None:return
        move=self.line_moves.pop(0);self.line_board.push(move)
        self.board.set_position(self.line_board,last_move=move,selected=None,best_move=None,review_sticker="Best line",sticker_color="#39a982")
        self.status.setText("Playing the engine’s suggested continuation, one move at a time.")
        if not self.line_moves:self.line_btn.setText("Line complete");self.line_btn.setEnabled(False)

    def next_position(self):
        self.index+=1;self._load_position()


class PuzzleDialog(QDialog):
    puzzle_requested=pyqtSignal(object)
    def __init__(self,theme_key,parent=None):
        super().__init__(parent);self.setWindowTitle("Offline Puzzles · Skill Practice");fit_dialog_to_screen(self,parent,1800,1250)
        self.token=0;self.puzzle=None;self.base_board=None;self.solution=None;self.selected=None;self.solved=False;self.revealed=False
        self.challenge_active=False;self.challenge_score=0;self.challenge_level=1;self.lives=3;self.recent_challenge_themes=[]
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();board_side=max(480,min(920,int(screen.availableGeometry().height()*.65))) if screen else 700
        root=QHBoxLayout(self);root.setContentsMargins(26,24,26,24);root.setSpacing(26)
        self.board=ChessBoard();self.board.setMinimumSize(board_side,board_side);self.board.set_theme(theme_key);self.board.square_clicked.connect(self.on_square);root.addWidget(self.board,1)
        panel=QFrame();panel.setObjectName("SidePanel");panel.setMinimumWidth(420);panel.setMaximumWidth(620);col=QVBoxLayout(panel);col.setContentsMargins(24,24,24,24);col.setSpacing(16)
        title=QLabel("PUZZLE STUDIO");title.setObjectName("Brand");col.addWidget(title)
        intro=QLabel("Choose a chess skill and difficulty. Puzzles mix positions from your games with new legal positions generated locally by Stockfish.");intro.setObjectName("Muted");intro.setWordWrap(True);col.addWidget(intro)
        skill_label=QLabel("Practice skill");skill_label.setObjectName("PlayerName");col.addWidget(skill_label)
        self.skill_choice=QComboBox()
        for label,value in (("Any tactic","Any tactic"),("Checkmate","Mate"),("Fork","Fork"),("Pin","Pin"),("Discovered attack","Discovered attack"),("Win material","Winning material"),("Promotion","Promotion")):
            self.skill_choice.addItem(label,value)
        col.addWidget(self.skill_choice)
        difficulty_row=QHBoxLayout();difficulty_title=QLabel("Difficulty");difficulty_title.setObjectName("PlayerName");difficulty_row.addWidget(difficulty_title);difficulty_row.addStretch();self.difficulty_label=QLabel("5 · Club");self.difficulty_label.setObjectName("AccentText");difficulty_row.addWidget(self.difficulty_label);col.addLayout(difficulty_row)
        self.difficulty=QSlider(Qt.Orientation.Horizontal);self.difficulty.setRange(1,10);self.difficulty.setValue(5);self.difficulty.valueChanged.connect(self._difficulty_changed);col.addWidget(self.difficulty)
        marks=QHBoxLayout();marks.addWidget(QLabel("Easy"));marks.addStretch();marks.addWidget(QLabel("Hard"));col.addLayout(marks)
        self.generate_btn=QPushButton("Generate puzzle");self.generate_btn.setObjectName("PrimaryButton");self.generate_btn.clicked.connect(self.generate);col.addWidget(self.generate_btn)
        self.challenge_btn=QPushButton("Start Challenge");self.challenge_btn.setObjectName("PrimaryButton");self.challenge_btn.clicked.connect(self.start_challenge);col.addWidget(self.challenge_btn)
        self.hint_btn=QPushButton("Hint");self.hint_btn.clicked.connect(self.show_hint);self.hint_btn.setEnabled(False);col.addWidget(self.hint_btn)
        self.solution_btn=QPushButton("Show solution");self.solution_btn.clicked.connect(self.show_solution);self.solution_btn.setEnabled(False);col.addWidget(self.solution_btn)
        self.progress=QProgressBar();self.progress.setTextVisible(True);self.progress.setFormat("Ready");self.progress.setValue(0);col.addWidget(self.progress)
        self.challenge_score_label=QLabel("Challenge score: 0");self.challenge_score_label.setObjectName("AccentText");col.addWidget(self.challenge_score_label)
        self.lives_label=QLabel("Lives: 3 / 3");self.lives_label.setObjectName("AccentText");col.addWidget(self.lives_label)
        self.status=QLabel("Generate a puzzle to start.");self.status.setObjectName("ReviewSummary");self.status.setWordWrap(True);self.status.setMinimumHeight(90);col.addWidget(self.status)
        self.line=QLabel("");self.line.setObjectName("Muted");self.line.setWordWrap(True);col.addWidget(self.line)
        col.addStretch();close=QPushButton("Close");close.clicked.connect(self.accept);col.addWidget(close);root.addWidget(panel)

    def _difficulty_changed(self,value):
        band="Beginner" if value<=2 else "Easy" if value<=4 else "Club" if value<=6 else "Challenging" if value<=8 else "Advanced"
        self.difficulty_label.setText(f"{value} · {band}")

    def generate(self):
        if self.generate_btn.isEnabled() is False:return
        self.token+=1;self.puzzle=None;self.base_board=None;self.solution=None;self.selected=None;self.solved=False;self.revealed=False
        self.board.setEnabled(False);self.board.set_position(chess.Board(),review_sticker=None);self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(False);self.line.setText("")
        if self.challenge_active:
            self.status.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10. Find the best move.")
        else:self.status.setText("Searching your local games and generating legal positions…")
        self.progress.setRange(0,0);self.progress.setFormat("Generating puzzle…");self.generate_btn.setEnabled(False)
        skill="Any tactic" if self.challenge_active else self.skill_choice.currentData()
        difficulty=self.challenge_level if self.challenge_active else self.difficulty.value()
        self.puzzle_requested.emit((self.token,skill,difficulty,self.challenge_active,list(self.recent_challenge_themes)))

    def start_challenge(self):
        if self.challenge_active:return
        self.challenge_active=True;self.challenge_score=0;self.challenge_level=1
        self.recent_challenge_themes=[]
        self.challenge_score_label.setText("Challenge score: 0 · Level 1/10")
        self.skill_choice.setEnabled(False);self.difficulty.setEnabled(False);self.challenge_btn.setEnabled(False)
        self.generate_btn.setText("Generating challenge…");self.generate()

    def _next_challenge_puzzle(self):
        if not self.challenge_active:return
        self.challenge_level=min(10,1+self.challenge_score)
        self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10")
        self.generate_btn.setText("Generating challenge…");self.generate_btn.setEnabled(True);self.generate()

    def _challenge_failed(self):
        self.challenge_active=False;self.board.setEnabled(False)
        self.skill_choice.setEnabled(True);self.difficulty.setEnabled(True)
        self.challenge_btn.setEnabled(True);self.challenge_btn.setText("Play Again")
        self.generate_btn.setText("Generate puzzle");self.generate_btn.setEnabled(True)
        self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(self.puzzle is not None)
        self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · final")
        self.status.setText(f"Challenge over · You solved {self.challenge_score} puzzle(s). You used all 3 lives on this puzzle.")

    def update_progress(self,token,done,total):
        if token!=self.token:return
        self.progress.setRange(0,max(1,total));self.progress.setValue(done);self.progress.setFormat(f"Checking positions · {done}/{total}")

    def show_puzzle(self,token,puzzle,error):
        if token!=self.token:return
        self.generate_btn.setEnabled(True);self.board.setEnabled(True);self.progress.setRange(0,1);self.progress.setValue(1)
        if not puzzle:
            self.puzzle=None;self.status.setText(error or "No matching puzzle was found.");self.progress.setFormat("No puzzle found")
            if self.challenge_active:
                self.status.setText(f"Could not generate a level {self.challenge_level} puzzle. Your score remains {self.challenge_score}; retrying the same level is safe.")
                self.generate_btn.setText("Retry challenge puzzle");self.generate_btn.setEnabled(True)
            return
        self.puzzle=puzzle;self.base_board=chess.Board(puzzle["fen"]);self.solution=chess.Move.from_uci(puzzle["move"]);self.selected=None;self.solved=False;self.revealed=False
        self.lives=3;self.lives_label.setText("Lives: 3 / 3")
        self.board.flipped=self.base_board.turn==chess.BLACK
        self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=None)
        level_note="Matches the selected difficulty." if puzzle.get("difficulty_match") else "Closest match found; difficulty is approximate."
        self.status.setText(f"{puzzle['theme']} · {puzzle['difficulty']}/10 · {level_note} Find the best move for the side to play.")
        self.progress.setFormat("Puzzle ready");self.hint_btn.setEnabled(not self.challenge_active);self.solution_btn.setEnabled(not self.challenge_active)
        if self.challenge_active:
            self.challenge_btn.setText(f"Challenge · {self.challenge_score} solved");self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10")
            self.recent_challenge_themes.append(puzzle["theme"]);self.recent_challenge_themes=self.recent_challenge_themes[-2:]
            level_note="" if puzzle.get("difficulty_match") else " · approximate level"
            self.status.setText(f"{puzzle['theme']} puzzle · score {self.challenge_score} · level {self.challenge_level}/10{level_note}. Find the best move.")
            self.generate_btn.setText("Challenge in progress");self.generate_btn.setEnabled(False)
        self.line.setText(f"The position came from {puzzle.get('source','a generated legal game position')}.")

    def on_square(self,square):
        if self.base_board is None or self.solved or self.revealed:return
        piece=self.base_board.piece_at(square)
        if self.selected is None:
            if piece and piece.color==self.base_board.turn:self.selected=square
        else:
            candidate=chess.Move(self.selected,square)
            if self.base_board.piece_type_at(self.selected)==chess.PAWN and chess.square_rank(square) in (0,7):
                candidate.promotion=self.solution.promotion if self.solution.from_square==self.selected and self.solution.to_square==square else chess.QUEEN
            if candidate in self.base_board.legal_moves:
                if candidate==self.solution:
                    self.solved=True;solved_board=self.base_board.copy();solved_board.push(candidate)
                    self.board.set_position(solved_board,last_move=candidate,selected=None,best_move=None,review_sticker="Correct",sticker_color="#39a982")
                    self.status.setText("Correct. You found the engine’s top move.");self.line.setText("Best line: "+" ".join(self.puzzle.get("pv",[])));self.selected=None
                    if self.challenge_active:
                        self.challenge_score+=1;self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · next level {min(10,1+self.challenge_score)}/10")
                        self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(False);self.board.setEnabled(False)
                        self.status.setText(f"Correct · score {self.challenge_score}. Next puzzle is harder…")
                        QTimer.singleShot(900,self._next_challenge_puzzle)
                    return
                if self.challenge_active:
                    self.lives-=1;self.lives_label.setText(f"Lives: {self.lives} / 3")
                    if self.lives<=0:
                        self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker="Out of lives",sticker_color="#de6268")
                        self._challenge_failed();return
                    self.status.setText(f"Not the best move · {self.lives} lives left. Keep looking.")
                    self.selected=None;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=f"{self.lives} lives left",sticker_color="#e0b746");return
                self.lives-=1;self.lives_label.setText(f"Lives: {self.lives} / 3")
                if self.lives<=0:
                    self.solved=True;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker="Out of lives",sticker_color="#de6268");self.board.setEnabled(False)
                    self.status.setText("Puzzle over · you used all 3 lives. Reveal the solution or generate another puzzle.")
                    self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(True);self.generate_btn.setEnabled(True);return
                self.status.setText(f"That move misses the strongest continuation · {self.lives} lives left. Try again or ask for a hint.")
                self.selected=None;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=f"{self.lives} lives left",sticker_color="#e0b746");return
            self.selected=square if piece and piece.color==self.base_board.turn else None
        self.board.set_position(self.base_board,last_move=None,selected=self.selected,best_move=None)

    def show_hint(self):
        if not self.puzzle:return
        hints={"Mate":"Look for forcing checks. Count the king’s escape squares after each check.",
               "Check":"Look for a forcing check that also creates a follow-up threat or wins time.",
               "Fork":"Look for a move that attacks two valuable targets at once, especially with check.",
               "Pin":"Can you line up a rook, bishop, or queen with a valuable piece behind the target?",
               "Discovered attack":"Move a blocker to uncover an attack from a rook, bishop, or queen.",
               "Winning material":"Look for a forcing capture or a threat that wins a valuable piece.",
               "Promotion":"Check whether a pawn can promote safely or create a promotion threat."}
        self.status.setText(hints.get(self.puzzle["theme"],"Look for checks, captures, and threats before choosing a quiet move."))

    def show_solution(self):
        if not self.puzzle:return
        self.revealed=True;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=self.solution,review_sticker="Best move",sticker_color="#39a982")
        self.status.setText(f"Best move: {self.puzzle['san']} · {', '.join(self.puzzle.get('themes',[]))}")
        self.line.setText("Best line: "+" ".join(self.puzzle.get("pv",[])))


class ReviewProgressDialog(QDialog):
    cancel_requested=pyqtSignal()
    def __init__(self,parent=None,batch_total=0,batch_started_at=None):
        super().__init__(parent);self.finished=False;self.batch_total=max(0,int(batch_total));self.batch_started_at=batch_started_at or time.monotonic();self.setWindowTitle("Reviewing downloaded games" if self.batch_total else "Reviewing game");self.setModal(True);self.setWindowModality(Qt.WindowModality.ApplicationModal);self.setFixedWidth(380)
        self.started_at=time.monotonic()
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint,False)
        layout=QVBoxLayout(self);title=QLabel("Analysing both sides, one move at a time…");title.setObjectName("PlayerName");layout.addWidget(title)
        self.status=QLabel("Preparing Stockfish review");self.status.setObjectName("Muted");layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);layout.addWidget(self.progress)
        self.batch_status=None;self.batch_progress=None
        if self.batch_total:
            self.batch_status=QLabel("Preparing batch estimate…");self.batch_status.setObjectName("Muted");self.batch_status.setWordWrap(True);layout.addWidget(self.batch_status)
            self.batch_progress=QProgressBar();self.batch_progress.setRange(0,self.batch_total*100);self.batch_progress.setValue(0);layout.addWidget(self.batch_progress)
        note=QLabel("Analysis runs locally. You can stop early and keep the moves already reviewed.");note.setObjectName("Muted");note.setWordWrap(True);layout.addWidget(note)
        self.cancel_button=QPushButton("Cancel review");self.cancel_button.clicked.connect(self.request_cancel);layout.addWidget(self.cancel_button)
    def begin_game(self):
        self.started_at=time.monotonic();self.status.setText("Preparing Stockfish review");self.progress.setRange(0,100);self.progress.setValue(0)
    def update_progress(self,done,total):
        elapsed=max(.01,time.monotonic()-self.started_at)
        remaining=max(0,int((elapsed/max(1,done))*(total-done)))
        estimate=f" · about {remaining}s left" if done else ""
        self.status.setText(f"Analysing move {done} of {total}{estimate}…");self.progress.setRange(0,max(1,total));self.progress.setValue(done)
    def update_batch_progress(self,completed_games,move_done=0,move_total=0):
        if not self.batch_total or not self.batch_progress or not self.batch_status:return
        fraction=min(float(self.batch_total),max(0.0,float(completed_games)))
        if move_total:fraction=min(float(self.batch_total),fraction+max(0,min(1,move_done/max(1,move_total))))
        self.batch_progress.setValue(round(fraction*100))
        elapsed=max(.01,time.monotonic()-self.batch_started_at)
        remaining=max(0,round((elapsed/max(fraction,.01))*(self.batch_total-fraction)))
        eta=(f"about {remaining//60}m {remaining%60}s remaining" if remaining>=60
             else f"about {remaining}s remaining")
        finished_games=int(fraction)
        if fraction>=self.batch_total:
            progress_text=f"{self.batch_total} of {self.batch_total} games complete"
        elif fraction>finished_games:
            current_percent=round((fraction-finished_games)*100)
            progress_text=f"Game {finished_games+1} of {self.batch_total} · {current_percent}% complete"
        else:
            progress_text=f"{finished_games} of {self.batch_total} games complete"
        self.batch_status.setText(f"{progress_text} · {eta}")
    def closeEvent(self,event):
        if not self.finished:event.ignore()
        else:event.accept()
    def request_cancel(self):
        if not self.finished and self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False);self.cancel_button.setText("Stopping…")
            self.status.setText("Stopping after the current position…")
            self.cancel_requested.emit()
    def finish(self):
        self.finished=True;self.accept()


class ReviewSummaryDialog(QDialog):
    def __init__(self,items,opening,result,user_color=chess.WHITE,parent=None,opening_ply=0,
                 analysis_level="Standard",syzygy_enabled=False):
        super().__init__(parent);self.setWindowTitle("Game Review · Summary");self.setModal(True);self.selected_ply=None;fit_dialog_to_screen(self,parent,1500,720)
        root=QVBoxLayout(self)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(0,0,0,0)
        scroll.setWidget(body);root.addWidget(scroll,1)
        title=QLabel("Game review complete");title.setObjectName("Brand");layout.addWidget(title)
        user_side="White" if user_color==chess.WHITE else "Black"
        if result=="1-0":outcome="You won as White" if user_color==chess.WHITE else "You lost as Black"
        elif result=="0-1":outcome="You won as Black" if user_color==chess.BLACK else "You lost as White"
        elif result=="1/2-1/2":outcome=f"Draw · you played as {user_side}"
        else:outcome=f"Game in progress · you played as {user_side}"
        tablebase_note=" · Syzygy available" if syzygy_enabled else ""
        subtitle=QLabel(f"{opening}  ·  {outcome}  ·  {analysis_level} analysis{tablebase_note}");subtitle.setObjectName("Muted");layout.addWidget(subtitle)
        cards=QHBoxLayout()
        def side_card(side):
            moves=[item for item in items if item.side==side]
            accuracy=review_accuracy(moves)
            errors=sum(item.verdict in ("Inaccuracy","Mistake","Blunder") for item in moves)
            missed=sum(item.verdict=="Miss" for item in moves)
            frame=QFrame();frame.setObjectName("MetricCard");column=QVBoxLayout(frame);column.setContentsMargins(16,12,16,12)
            perspective="YOU" if side==user_side else "OPPONENT"
            label=QLabel(f"{perspective} · {side.upper()} · MOVE QUALITY");label.setObjectName("MetricCaption");column.addWidget(label)
            score=QLabel(f"{accuracy:.1f}%" if accuracy is not None else "—");score.setObjectName("MetricValue");column.addWidget(score)
            note=QLabel(f"{errors} error{'s' if errors!=1 else ''} · {missed} missed chance{'s' if missed!=1 else ''} · {len(moves)} moves");note.setObjectName("Muted");column.addWidget(note)
            frame.setToolTip(MOVE_QUALITY_TOOLTIP)
            return frame
        opponent_side="Black" if user_side=="White" else "White"
        cards.addWidget(side_card(user_side));cards.addWidget(side_card(opponent_side));layout.addLayout(cards)
        quality_note=QLabel("Local move quality is a CAPS2-inspired grade from Stockfish evaluation changes, not a literal expected-score percentage or player rating.")
        quality_note.setObjectName("Muted");quality_note.setWordWrap(True);layout.addWidget(quality_note)
        table=QTableWidget(2,13);table.setHorizontalHeaderLabels(["Side","Move quality","Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracies","Mistakes","Blunders","Book","Forced"])
        table.setToolTip(MOVE_QUALITY_TOOLTIP)
        table.verticalHeader().hide();table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection);table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);table.horizontalHeader().setStretchLastSection(True)
        for row,side in enumerate(("White","Black")):
            moves=[item for item in items if item.side==side]
            accuracy=review_accuracy(moves)
            counts={tag:sum(1 for item in moves if item.verdict==tag) for tag in ("Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracy","Mistake","Blunder","Forced")}
            book=sum(1 for item in moves if item.book_status.startswith("Book move"))
            cells=[side,f"{accuracy:.1f}%" if accuracy is not None else "—",str(counts["Brilliant"]),str(counts["Great"]),str(counts["Best"]),str(counts["Excellent"]),str(counts["Good"]),str(counts["Miss"]),str(counts["Inaccuracy"]),str(counts["Mistake"]),str(counts["Blunder"]),str(book),str(counts["Forced"])]
            for col,value in enumerate(cells):table.setItem(row,col,QTableWidgetItem(value))
        layout.addWidget(table)
        narrative=QLabel("Game story: "+review_game_narrative(items,user_color));narrative.setObjectName("ReviewSummary");narrative.setWordWrap(True);layout.addWidget(narrative)
        coach=QLabel("Game-specific lessons: "+game_coach_summary(items,user_color));coach.setObjectName("ReviewSummary");coach.setWordWrap(True);layout.addWidget(coach)
        opening_items=[item for item in items if item.book_status.startswith("Book move")]
        opening_item=(max(opening_items,key=lambda item:item.ply) if opening_items else
                      next((item for item in items if item.ply==opening_ply),None))
        turning=rank_review_moments(items,limit=7 if opening_item else 8)
        if opening_item and all(item.ply!=opening_item.ply for item in turning):
            turning.append(opening_item);turning.sort(key=lambda item:item.ply)
        moment_tags=review_moment_tags(items,limit=7 if opening_item else 8)
        if opening_item:
            opening_tag=("Last book move" if opening_item.book_status.startswith("Book move")
                         else "Last documented opening move")
            opening_labels=moment_tags.setdefault(opening_item.ply,[])
            if opening_tag not in opening_labels:opening_labels.append(opening_tag)
        highlights=[]
        for item in turning[:8]:
            tags=" · ".join(moment_tags.get(item.ply,[item.verdict]))
            impact=review_moment_impact(item)
            highlights.append(f"{review_move_notation(item)} ({item.side}): {tags}"+
                              (f" ({impact})" if impact else ""))
        summary=QLabel("Key moments: "+(" · ".join(highlights) if highlights else "No major turning points flagged."));summary.setObjectName("ReviewSummary");summary.setWordWrap(True);layout.addWidget(summary)
        if turning:
            moments_layout=QGridLayout();moments_layout.setHorizontalSpacing(8);moments_layout.setVerticalSpacing(8)
            for index,item in enumerate(turning[:8]):
                tags=" · ".join(moment_tags.get(item.ply,[item.verdict]));impact=review_moment_impact(item)
                button=QPushButton(f"{review_move_notation(item)} · {tags}"+(f" · {impact}" if impact else ""))
                reason=review_text_for_viewer(item.explanation.strip(),item,user_color) or f"Stockfish's preferred move was {item.best_san}."
                impact_note=f" · {item.expected_loss*100:.1f} pp expected score lost" if impact else ""
                button.setToolTip(f"{item.side} to move{impact_note}\nAnalysis: {getattr(item,'confidence','Standard analysis')}\n{reason}\nOpen this move in the guided review")
                button.clicked.connect(lambda _checked=False,ply=item.ply:self._open_moment(ply))
                moments_layout.addWidget(button,index//4,index%4)
            layout.addLayout(moments_layout)
        if not any(item.book_status!="Book not loaded" for item in items):
            note=QLabel("Book moves need a weighted Polyglot .bin file. Lichess TSVs identify openings but don’t include move weights.");note.setObjectName("Muted");layout.addWidget(note)
        row=QHBoxLayout();row.addStretch()
        if turning:
            guided_button=QPushButton("Start with key moments")
            guided_button.setObjectName("PrimaryButton")
            guided_button.setToolTip("Begin with the first critical move; use the key-moment controls to continue through the game.")
            guided_button.clicked.connect(lambda _checked=False,ply=turning[0].ply:self._open_moment(ply))
            row.addWidget(guided_button)
            self.start_button=QPushButton("Review every move")
            self.start_button.setToolTip("Open the full move-by-move review from the beginning of the game.")
        else:
            self.start_button=QPushButton("Start Review")
            self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.accept);row.addWidget(self.start_button);root.addLayout(row)

    def _open_moment(self,ply):
        self.selected_ply=ply;self.accept()


class ProfileDialog(QDialog):
    def __init__(self,store,open_game_callback,theme_changed_callback=None,presentation_changed_callback=None,parent=None):
        super().__init__(parent);self.store=store;self.open_game_callback=open_game_callback;self.theme_changed_callback=theme_changed_callback;self.presentation_changed_callback=presentation_changed_callback
        self.setWindowTitle("Player Profile · Offline Chess");fit_dialog_to_screen(self,parent,1600,1150)
        root_layout=QVBoxLayout(self);self.tabs=QTabWidget();root_layout.addWidget(self.tabs)
        overview_page=QWidget();layout=QVBoxLayout(overview_page);self.tabs.addTab(overview_page,"Overview")
        self.rating=QLabel();self.rating.setObjectName("Brand");layout.addWidget(self.rating)
        self.rating_note=QLabel();self.rating_note.setObjectName("Muted");layout.addWidget(self.rating_note)
        theme_row=QHBoxLayout();self.theme_preview=ChessBoard();self.theme_preview.setMinimumSize(210,210);self.theme_preview.setMaximumSize(250,250);theme_row.addWidget(self.theme_preview)
        theme_controls=QVBoxLayout();theme_title=QLabel("BOARD STYLE");theme_title.setObjectName("SectionTitle");theme_controls.addWidget(theme_title)
        self.theme_choice=QComboBox()
        for key,theme in BOARD_THEMES.items():self.theme_choice.addItem(theme["name"],key)
        saved_theme=store.get_setting("board_theme","classic_green")
        selected_theme=saved_theme if saved_theme in BOARD_THEMES else "classic_green"
        self.theme_preview.set_theme(selected_theme)
        self.theme_choice.setCurrentIndex(max(0,self.theme_choice.findData(selected_theme)))
        self.theme_choice.currentIndexChanged.connect(self.change_theme);theme_controls.addWidget(self.theme_choice)
        piece_label=QLabel("PIECE STYLE");piece_label.setObjectName("SectionTitle");theme_controls.addWidget(piece_label)
        self.piece_choice=QComboBox()
        for key,style in PIECE_STYLES.items():self.piece_choice.addItem(style["name"],key)
        saved_piece=store.get_setting("piece_style","tournament")
        self.piece_choice.setCurrentIndex(max(0,self.piece_choice.findData(saved_piece if saved_piece in PIECE_STYLES else "tournament")));theme_controls.addWidget(self.piece_choice)
        self.coordinate_toggle=QCheckBox("Show board coordinates");self.coordinate_toggle.setChecked(store.get_setting("show_coordinates","1")!="0");theme_controls.addWidget(self.coordinate_toggle)
        self.motion_toggle=QCheckBox("Animate move highlights");self.motion_toggle.setChecked(store.get_setting("board_animations","1")!="0");theme_controls.addWidget(self.motion_toggle)
        self.theme_preview.set_presentation(self.piece_choice.currentData(),self.coordinate_toggle.isChecked(),self.motion_toggle.isChecked())
        self.piece_choice.currentIndexChanged.connect(self.change_presentation);self.coordinate_toggle.toggled.connect(self.change_presentation);self.motion_toggle.toggled.connect(self.change_presentation)
        theme_note=QLabel("Applies to the game, review, and opening boards. Your choice is saved on this computer.");theme_note.setObjectName("Muted");theme_note.setWordWrap(True);theme_controls.addWidget(theme_note)
        theme_row.addLayout(theme_controls,1);theme_row.addStretch();layout.addLayout(theme_row)
        self.insights=QLabel();self.insights.setObjectName("ReviewSummary");self.insights.setWordWrap(True);layout.addWidget(self.insights)
        filters=QHBoxLayout();self.date_filter=QComboBox();self.date_filter.addItems(["All time","Last 30 days","Last 90 days","This year"]);filters.addWidget(self.date_filter)
        self.result_filter=QComboBox();self.result_filter.addItems(["All results","Wins","Draws","Losses"]);filters.addWidget(self.result_filter)
        self.color_filter=QComboBox();self.color_filter.addItems(["Both colors","As White","As Black"]);filters.addWidget(self.color_filter)
        self.opening_filter=QComboBox();self.opening_filter.addItem("All openings");filters.addWidget(self.opening_filter)
        self.opponent_filter=QComboBox();self.opponent_filter.addItems(["Any opponent","Below 1000","1000–1599","1600–2199","2200+"]);filters.addWidget(self.opponent_filter);filters.addStretch();layout.addLayout(filters)
        for control in (self.date_filter,self.result_filter,self.color_filter,self.opening_filter,self.opponent_filter):control.currentIndexChanged.connect(self.refresh)
        self.chart=AccuracyChart();layout.addWidget(self.chart)
        self.chart.setAccessibleName("Move quality history")
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(["Date","Result","Your move quality","Opponent","Opening","Avg loss","Full game"])
        self.table.verticalHeader().hide();self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setAlternatingRowColors(True);self.table.setShowGrid(False)
        header=self.table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch);header.setSectionResizeMode(5,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(6,QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda *_:self.open_selected());layout.addWidget(self.table,1)
        settings=QHBoxLayout();self.store_checkbox=QCheckBox("Keep full PGNs in profile (uses more space)");self.store_checkbox.setChecked(store.full_pgns_enabled());self.store_checkbox.toggled.connect(self.store.set_full_pgns);settings.addWidget(self.store_checkbox);settings.addStretch()
        self.open_btn=QPushButton("Open saved game");self.open_btn.clicked.connect(self.open_selected);settings.addWidget(self.open_btn)
        self.delete_selected_btn=QPushButton("Delete selected game");self.delete_selected_btn.clicked.connect(self.delete_selected);settings.addWidget(self.delete_selected_btn)
        self.clear_btn=QPushButton("Delete stored PGNs");self.clear_btn.clicked.connect(self.clear_pgn_data);settings.addWidget(self.clear_btn);layout.addLayout(settings)
        self.clear_all_btn=QPushButton("Delete all profile data");self.clear_all_btn.clicked.connect(self.clear_all_data);layout.addWidget(self.clear_all_btn)
        self._build_chesscom_page()
        self.tabs.currentChanged.connect(self._load_chesscom_on_tab)
        self.refresh()

    def _load_chesscom_on_tab(self,index):
        if index!=self.tabs.indexOf(self.chesscom_scroll) or self._chesscom_data_loaded:return
        self._chesscom_data_loaded=True
        username=self.chesscom_username.text().strip()
        if not username:return
        self.chesscom_status.setText("Loading Chess.com insights and downloaded games…")
        QTimer.singleShot(0,lambda:self.refresh_chesscom_library(username))

    def _build_chesscom_page(self):
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumSize(0,0);scroll.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(16,16,16,16);layout.setSpacing(10)
        title=QLabel("CHESS.COM INSIGHTS & GAME LIBRARY");title.setObjectName("SectionTitle");layout.addWidget(title)
        note=QLabel("Fetch public game archives by username. Games are saved as individual PGN files on this computer. Chess.com archive data can lag behind live play. This app is independent and not affiliated with Chess.com.")
        note.setObjectName("Muted");note.setWordWrap(True);layout.addWidget(note)
        account_row=QHBoxLayout();self.chesscom_username=QLineEdit(self.store.get_setting("chesscom_username",""))
        self.chesscom_username.setPlaceholderText("Chess.com username");self.chesscom_username.setClearButtonEnabled(True)
        self.chesscom_username.editingFinished.connect(lambda:self.refresh_chesscom_library(self.chesscom_username.text()))
        account_row.addWidget(self.chesscom_username,1);self.chesscom_connect=QPushButton("Find archives");self.chesscom_connect.clicked.connect(self.find_chesscom_archives);account_row.addWidget(self.chesscom_connect)
        account_row.addWidget(QLabel("Month"));self.chesscom_month=QComboBox();self.chesscom_month.setMinimumWidth(140);self.chesscom_month.currentIndexChanged.connect(self.load_chesscom_month);account_row.addWidget(self.chesscom_month)
        self.chesscom_download_all=QPushButton("Download all months");self.chesscom_download_all.clicked.connect(self.download_all_chesscom);account_row.addWidget(self.chesscom_download_all)
        self.chesscom_update_downloads=QPushButton("Update downloaded games");self.chesscom_update_downloads.setEnabled(False);self.chesscom_update_downloads.setToolTip("Check archive months from your latest saved game and download only games not already stored.");self.chesscom_update_downloads.clicked.connect(self.update_downloaded_chesscom);account_row.addWidget(self.chesscom_update_downloads)
        layout.addLayout(account_row)
        self.chesscom_status=QLabel("Enter your Chess.com username to find available monthly archives.");self.chesscom_status.setObjectName("Muted");self.chesscom_status.setWordWrap(True);layout.addWidget(self.chesscom_status)
        self.chesscom_progress=QProgressBar();self.chesscom_progress.setTextVisible(True);self.chesscom_progress.setVisible(False);layout.addWidget(self.chesscom_progress)
        snapshot_header=QHBoxLayout();snapshot_title=QLabel("PERFORMANCE SNAPSHOT");snapshot_title.setObjectName("SectionTitle");snapshot_header.addWidget(snapshot_title,1)
        snapshot_header.addWidget(QLabel("Insights period"));self.chesscom_date_filter=QComboBox();self.chesscom_date_filter.addItems(["All time","Last 7 days","Last 30 days","Last 90 days","Last 365 days","This year"]);snapshot_header.addWidget(self.chesscom_date_filter)
        layout.addLayout(snapshot_header)
        metric_grid=QGridLayout();metric_grid.setHorizontalSpacing(8);metric_grid.setVerticalSpacing(8)
        self.chesscom_metric_values={};self.chesscom_metric_notes={}
        for index,(key,title) in enumerate((("games","GAMES"),("score","SCORE"),("win_rate","WIN RATE"),
                                            ("chesscom_accuracy","CHESS.COM ACCURACY"),("accuracy","LOCAL REVIEW ACCURACY"),
                                            ("reviewed","GAMES REVIEWED"),("rating","LATEST RATING"),
                                            ("opponent","AVG OPPONENT"),("best_win","BEST WIN"),("streak","WINNING STREAK"))):
            card=QFrame();card.setObjectName("SidePanel");card_layout=QVBoxLayout(card);card_layout.setContentsMargins(12,9,12,9);card_layout.setSpacing(2)
            heading=QLabel(title);heading.setObjectName("Muted");card_layout.addWidget(heading)
            value=QLabel("—");value.setObjectName("Brand");card_layout.addWidget(value)
            detail=QLabel("");detail.setObjectName("Muted");detail.setWordWrap(True);card_layout.addWidget(detail)
            self.chesscom_metric_values[key]=value;self.chesscom_metric_notes[key]=detail
            metric_grid.addWidget(card,index//3,index%3)
        layout.addLayout(metric_grid)
        past_title=QLabel("PAST PERFORMANCE");past_title.setObjectName("SectionTitle");layout.addWidget(past_title)
        self.chesscom_period_comparison=QLabel("Choose a recent insights period to compare it with the preceding period.");self.chesscom_period_comparison.setObjectName("ReviewSummary");self.chesscom_period_comparison.setWordWrap(True);layout.addWidget(self.chesscom_period_comparison)
        self.chesscom_trends=QLabel("Download games to build your rating, results, and opening trends.");self.chesscom_trends.setObjectName("ReviewSummary");self.chesscom_trends.setWordWrap(True);layout.addWidget(self.chesscom_trends)
        results_title=QLabel("RESULTS BY MONTH · WIN / DRAW / LOSS");results_title.setObjectName("SectionTitle");layout.addWidget(results_title)
        self.chesscom_results_chart=ChessComResultsChart();layout.addWidget(self.chesscom_results_chart)
        accuracy_trend_title=QLabel("ACCURACY OVER TIME");accuracy_trend_title.setObjectName("SectionTitle");layout.addWidget(accuracy_trend_title)
        accuracy_split=QHBoxLayout();accuracy_split.setSpacing(12)
        local_accuracy_panel=QVBoxLayout();local_accuracy_title=QLabel("LOCAL REVIEW");local_accuracy_title.setObjectName("Muted");local_accuracy_panel.addWidget(local_accuracy_title)
        self.chesscom_accuracy_chart=ChessComAccuracyChart("local review accuracy per month");local_accuracy_panel.addWidget(self.chesscom_accuracy_chart)
        chesscom_accuracy_panel=QVBoxLayout();chesscom_accuracy_title=QLabel("CHESS.COM");chesscom_accuracy_title.setObjectName("Muted");chesscom_accuracy_panel.addWidget(chesscom_accuracy_title)
        self.chesscom_native_accuracy_chart=ChessComAccuracyChart("Chess.com accuracy per month");chesscom_accuracy_panel.addWidget(self.chesscom_native_accuracy_chart)
        accuracy_split.addLayout(local_accuracy_panel,1);accuracy_split.addLayout(chesscom_accuracy_panel,1);layout.addLayout(accuracy_split)
        quality_title=QLabel("MOVE QUALITY OVER TIME");quality_title.setObjectName("SectionTitle");layout.addWidget(quality_title)
        self.chesscom_quality_trend=ChessComQualityTrendChart();layout.addWidget(self.chesscom_quality_trend)
        rating_header=QHBoxLayout();self.chesscom_rating_label=QLabel("RATING HISTORY");self.chesscom_rating_label.setObjectName("SectionTitle");rating_header.addWidget(self.chesscom_rating_label,1)
        rating_header.addWidget(QLabel("Time control"));self.chesscom_speed_filter=QComboBox();self.chesscom_speed_filter.addItems(["All time controls","Bullet","Blitz","Rapid","Daily"]);self.chesscom_speed_filter.setMinimumWidth(190);rating_header.addWidget(self.chesscom_speed_filter);layout.addLayout(rating_header)
        self.chesscom_elo_chart=EloHistoryChart();layout.addWidget(self.chesscom_elo_chart)
        control_title=QLabel("PERFORMANCE BY TIME CONTROL");control_title.setObjectName("SectionTitle");layout.addWidget(control_title)
        self.chesscom_controls=QTableWidget(0,5);self.chesscom_controls.setHorizontalHeaderLabels(["Time control","Games","W–D–L","Score","Review accuracy"])
        self._configure_chesscom_table(self.chesscom_controls);self.chesscom_controls.setMaximumHeight(190);layout.addWidget(self.chesscom_controls)
        split_insights=QHBoxLayout();split_insights.setSpacing(12)
        color_panel=QVBoxLayout();color_title=QLabel("RESULTS BY COLOR");color_title.setObjectName("SectionTitle");color_panel.addWidget(color_title)
        self.chesscom_colors=QTableWidget(0,4);self.chesscom_colors.setHorizontalHeaderLabels(["Color","Games","W–D–L","Score"])
        self._configure_chesscom_table(self.chesscom_colors);self.chesscom_colors.setMaximumHeight(160);color_panel.addWidget(self.chesscom_colors)
        rating_panel=QVBoxLayout();rating_band_title=QLabel("RESULT VS RATING EXPECTATION");rating_band_title.setObjectName("SectionTitle")
        rating_band_title.setToolTip("Expected score is estimated with the Elo formula and the ratings listed in each game archive. It is a model baseline, not a comparison with similar Chess.com members.")
        rating_panel.addWidget(rating_band_title)
        self.chesscom_opponent_bands=QTableWidget(0,6);self.chesscom_opponent_bands.setHorizontalHeaderLabels(["Opponent rating","Games","W–D–L","Actual score","Expected","Difference"])
        self._configure_chesscom_table(self.chesscom_opponent_bands);self.chesscom_opponent_bands.setMaximumHeight(160);rating_panel.addWidget(self.chesscom_opponent_bands)
        split_insights.addLayout(color_panel,1);split_insights.addLayout(rating_panel,1);layout.addLayout(split_insights)
        geography_title=QLabel("OPPONENT COUNTRIES · PUBLIC PROFILE DATA");geography_title.setObjectName("SectionTitle");layout.addWidget(geography_title)
        geography_note=QLabel("Country is the country opponents selected on their Chess.com profiles. Click Lookup opponent countries to fetch missing public profile details.");geography_note.setObjectName("Muted");geography_note.setWordWrap(True);layout.addWidget(geography_note)
        self.chesscom_geography=QTableWidget(0,5);self.chesscom_geography.setHorizontalHeaderLabels(["Country","Games","W–D–L","Score","Review accuracy"])
        self._configure_chesscom_table(self.chesscom_geography);self.chesscom_geography.setMaximumHeight(240);layout.addWidget(self.chesscom_geography)
        country_actions=QHBoxLayout();self.chesscom_country_lookup=QPushButton("Lookup opponent countries");self.chesscom_country_lookup.clicked.connect(self.lookup_chesscom_opponent_countries);country_actions.addWidget(self.chesscom_country_lookup)
        self.chesscom_country_cancel=QPushButton("Stop lookups");self.chesscom_country_cancel.clicked.connect(self.cancel_chesscom_country_lookup);self.chesscom_country_cancel.setVisible(False);country_actions.addWidget(self.chesscom_country_cancel);country_actions.addStretch();layout.addLayout(country_actions)
        result_title=QLabel("HOW GAMES ENDED");result_title.setObjectName("SectionTitle");layout.addWidget(result_title)
        self.chesscom_termination=QTableWidget(0,4);self.chesscom_termination.setHorizontalHeaderLabels(["Termination","Games","W–D–L","Share"])
        self._configure_chesscom_table(self.chesscom_termination);self.chesscom_termination.setMaximumHeight(240);layout.addWidget(self.chesscom_termination)
        calendar_title=QLabel("RESULTS BY DAY AND TIME");calendar_title.setObjectName("SectionTitle");layout.addWidget(calendar_title)
        self.chesscom_calendar=QTableWidget(0,5);self.chesscom_calendar.setHorizontalHeaderLabels(["Day / local time","Games","W–D–L","Score","Review accuracy"])
        self._configure_chesscom_table(self.chesscom_calendar);self.chesscom_calendar.setMaximumHeight(330);layout.addWidget(self.chesscom_calendar)
        review_title=QLabel("REVIEWED PLAY · PHASES, TACTICS, AND MOVE LABELS");review_title.setObjectName("SectionTitle");layout.addWidget(review_title)
        move_accuracy_title=QLabel("ACCURACY BY MOVE NUMBER");move_accuracy_title.setObjectName("SectionTitle");layout.addWidget(move_accuracy_title)
        self.chesscom_move_accuracy_chart=ChessComAccuracyChart("per move number");layout.addWidget(self.chesscom_move_accuracy_chart)
        piece_accuracy_title=QLabel("ACCURACY BY PIECE");piece_accuracy_title.setObjectName("SectionTitle");layout.addWidget(piece_accuracy_title)
        self.chesscom_piece_accuracy=QTableWidget(0,3);self.chesscom_piece_accuracy.setHorizontalHeaderLabels(["Piece","Reviewed moves","Average accuracy"])
        self._configure_chesscom_table(self.chesscom_piece_accuracy);self.chesscom_piece_accuracy.setMaximumHeight(190);layout.addWidget(self.chesscom_piece_accuracy)
        castling_header=QHBoxLayout();castling_title=QLabel("CASTLING TIMING AND RESULTS · CLICK A ROW TO FILTER GAMES");castling_title.setObjectName("SectionTitle");castling_header.addWidget(castling_title,1)
        self.chesscom_castling_scan=QPushButton("Quick scan");self.chesscom_castling_scan.setEnabled(False);self.chesscom_castling_scan.setToolTip("Read saved Chess.com PGNs to find when castling happened. No engine review is run.");self.chesscom_castling_scan.clicked.connect(self.quick_scan_chesscom_castling);castling_header.addWidget(self.chesscom_castling_scan)
        self.chesscom_castling_scan_status=QLabel("Scans are saved and reused across dashboard refreshes.");self.chesscom_castling_scan_status.setObjectName("Muted");castling_header.addWidget(self.chesscom_castling_scan_status);layout.addLayout(castling_header)
        self.chesscom_castling_scan_progress=QProgressBar();self.chesscom_castling_scan_progress.setVisible(False);layout.addWidget(self.chesscom_castling_scan_progress)
        self.chesscom_castling=QTableWidget(0,4);self.chesscom_castling.setHorizontalHeaderLabels(["Castling","Games","W–D–L","Score"])
        self._configure_chesscom_table(self.chesscom_castling);self.chesscom_castling.setMaximumHeight(190)
        self.chesscom_castling.cellClicked.connect(self._filter_chesscom_library_by_castling);layout.addWidget(self.chesscom_castling)
        shape_header=QHBoxLayout();shape_title=QLabel("GAME FLOW PATTERNS");shape_title.setObjectName("SectionTitle");shape_header.addWidget(shape_title,1)
        shape_header.addWidget(QLabel("Trace"));self.chesscom_shape_pattern=QComboBox();self.chesscom_shape_pattern.setMinimumWidth(210)
        self.chesscom_shape_pattern.currentTextChanged.connect(self._set_chesscom_shape_pattern);shape_header.addWidget(self.chesscom_shape_pattern);layout.addLayout(shape_header)
        shape_note=QLabel("Estimated from local engine review traces; the chart shows average user-side expected score through the selected pattern. Shares use reviewed games with at least three evaluation points. Chess.com’s game-shape labels are proprietary.");shape_note.setObjectName("Muted");shape_note.setWordWrap(True);layout.addWidget(shape_note)
        self.chesscom_shape_chart=ChessComShapeChart();layout.addWidget(self.chesscom_shape_chart)
        self.chesscom_game_shapes=QTableWidget(0,5);self.chesscom_game_shapes.setHorizontalHeaderLabels(["Pattern","Games","Share","W–D–L","Average accuracy"])
        self._configure_chesscom_table(self.chesscom_game_shapes);self.chesscom_game_shapes.setMaximumHeight(230);layout.addWidget(self.chesscom_game_shapes)
        self.chesscom_review_insights=QTableWidget(0,4);self.chesscom_review_insights.setHorizontalHeaderLabels(["Measure","Games / moves","Average / count","Share"])
        self._configure_chesscom_table(self.chesscom_review_insights);self.chesscom_review_insights.setMaximumHeight(260);layout.addWidget(self.chesscom_review_insights)
        tactic_examples_title=QLabel("TACTICAL REVIEW EXAMPLES · DOUBLE-CLICK TO REVIEW");tactic_examples_title.setObjectName("SectionTitle");layout.addWidget(tactic_examples_title)
        self.chesscom_tactic_examples=QTableWidget(0,5);self.chesscom_tactic_examples.setHorizontalHeaderLabels(["Finding","Date","Side","Move","Game / detail"])
        self._configure_chesscom_table(self.chesscom_tactic_examples);self.chesscom_tactic_examples.setMaximumHeight(330)
        self.chesscom_tactic_examples.cellDoubleClicked.connect(self._open_chesscom_tactic_example);layout.addWidget(self.chesscom_tactic_examples)
        opening_title=QLabel("OPENING RESULTS");opening_title.setObjectName("SectionTitle");layout.addWidget(opening_title)
        self.chesscom_opening_mastery=QLabel("Opening mastery estimate uses the loaded local opening database.")
        self.chesscom_opening_mastery.setObjectName("ReviewSummary");self.chesscom_opening_mastery.setWordWrap(True)
        self.chesscom_opening_mastery.setToolTip("Counts your moves that remain in the loaded Lichess opening lines, only in games where your move is the first to leave the local book.")
        layout.addWidget(self.chesscom_opening_mastery)
        self.chesscom_openings=QTableWidget(0,7);self.chesscom_openings.setHorizontalHeaderLabels(["Opening","Color","Games","W–D–L","Score","Local opening accuracy","Local book depth"])
        self.chesscom_openings.setToolTip("Double-click an opening to filter the downloaded game library to that line.")
        self._configure_chesscom_table(self.chesscom_openings);self.chesscom_openings.setMaximumHeight(240)
        self.chesscom_openings.cellClicked.connect(self._set_chesscom_opening_trend)
        self.chesscom_openings.cellDoubleClicked.connect(self._open_chesscom_opening_games);layout.addWidget(self.chesscom_openings)
        opening_trend_title=QLabel("OPENING PERFORMANCE OVER TIME · SELECT A ROW");opening_trend_title.setObjectName("SectionTitle");layout.addWidget(opening_trend_title)
        opening_trend_split=QHBoxLayout();opening_trend_split.setSpacing(12)
        self.chesscom_opening_results_chart=ChessComResultsChart();self.chesscom_opening_results_chart.setToolTip("Monthly win, draw, and loss proportions for the selected opening and color.")
        self.chesscom_opening_accuracy_chart=ChessComAccuracyChart("opening-phase accuracy per month")
        opening_trend_split.addWidget(self.chesscom_opening_results_chart,1);opening_trend_split.addWidget(self.chesscom_opening_accuracy_chart,1)
        layout.addLayout(opening_trend_split)
        available_title=QLabel("GAMES IN SELECTED MONTH");available_title.setObjectName("SectionTitle");layout.addWidget(available_title)
        self.chesscom_available=QTableWidget(0,6);self.chesscom_available.setHorizontalHeaderLabels(["Date","Opponent","Result","Time control","Rating","Rated"])
        self._configure_chesscom_table(self.chesscom_available);layout.addWidget(self.chesscom_available,1)
        remote_actions=QHBoxLayout();self.chesscom_download_selected=QPushButton("Download selected games");self.chesscom_download_selected.clicked.connect(self.download_selected_chesscom);remote_actions.addWidget(self.chesscom_download_selected)
        self.chesscom_download_month=QPushButton("Download this month");self.chesscom_download_month.clicked.connect(self.download_month_chesscom);remote_actions.addWidget(self.chesscom_download_month);remote_actions.addStretch();layout.addLayout(remote_actions)
        library_title=QLabel("DOWNLOADED GAMES · SAVED AS INDIVIDUAL PGNS");library_title.setObjectName("SectionTitle");layout.addWidget(library_title)
        library_filters=QHBoxLayout();self.chesscom_search=QLineEdit();self.chesscom_search.setPlaceholderText("Find opponent or opening…");self.chesscom_search.setClearButtonEnabled(True);library_filters.addWidget(self.chesscom_search,1)
        self.chesscom_review_filter=QComboBox();self.chesscom_review_filter.addItems(["All downloaded","Not reviewed","Reviewed"]);library_filters.addWidget(self.chesscom_review_filter);layout.addLayout(library_filters)
        self._chesscom_library_page=0;self._chesscom_library_matches=[];self._chesscom_library_unfiltered_matches=[]
        self._chesscom_castling_filter=None;self._chesscom_castling_phase_by_id=None;self._chesscom_all_games=[]
        library_paging=QHBoxLayout();self.chesscom_library_page_label=QLabel("Downloaded games load here.");library_paging.addWidget(self.chesscom_library_page_label,1)
        self.chesscom_library_previous=QPushButton("Previous 100");self.chesscom_library_previous.clicked.connect(lambda:self._change_chesscom_library_page(-1));library_paging.addWidget(self.chesscom_library_previous)
        self.chesscom_library_next=QPushButton("Next 100");self.chesscom_library_next.clicked.connect(lambda:self._change_chesscom_library_page(1));library_paging.addWidget(self.chesscom_library_next)
        layout.addLayout(library_paging)
        self.chesscom_library=QTableWidget(0,9);self.chesscom_library.setHorizontalHeaderLabels(["Date","Opponent","Result","Time control","Your rating","Opening","Review","Rules","PGN"])
        self._configure_chesscom_table(self.chesscom_library);self.chesscom_library.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.chesscom_library.cellDoubleClicked.connect(lambda *_:self.open_selected_chesscom());layout.addWidget(self.chesscom_library,2)
        self.chesscom_search.textChanged.connect(self._refresh_chesscom_from_filters)
        self.chesscom_speed_filter.currentIndexChanged.connect(self._refresh_chesscom_from_filters)
        self.chesscom_date_filter.currentIndexChanged.connect(self._refresh_chesscom_from_filters)
        self.chesscom_review_filter.currentIndexChanged.connect(self._refresh_chesscom_from_filters)
        library_actions=QHBoxLayout();self.chesscom_open=QPushButton("Open selected game");self.chesscom_open.clicked.connect(self.open_selected_chesscom);library_actions.addWidget(self.chesscom_open)
        self.chesscom_review_selected=QPushButton("Review selected");self.chesscom_review_selected.clicked.connect(self.review_selected_chesscom);library_actions.addWidget(self.chesscom_review_selected)
        self.chesscom_review_unreviewed=QPushButton("Review all unreviewed");self.chesscom_review_unreviewed.clicked.connect(self.review_unreviewed_chesscom);library_actions.addWidget(self.chesscom_review_unreviewed)
        self.chesscom_folder=QPushButton("Open PGN folder");self.chesscom_folder.clicked.connect(self.open_chesscom_folder);library_actions.addWidget(self.chesscom_folder)
        self.chesscom_delete=QPushButton("Remove selected");self.chesscom_delete.clicked.connect(self.delete_selected_chesscom);library_actions.addWidget(self.chesscom_delete);library_actions.addStretch();layout.addLayout(library_actions)
        scroll.setWidget(page);self.chesscom_scroll=scroll;self.tabs.addTab(scroll,"Chess.com")
        self._chesscom_archives=[];self._chesscom_available_games=[];self._chesscom_worker=None;self._chesscom_known_game_ids=set();self._chesscom_latest_game_month="";self._castling_scan_worker=None;self._chesscom_castling_scan_records=[];self._chesscom_castling_scan_summary="";self._chesscom_download_errors=0;self._chesscom_review_cache={};self._chesscom_pgn_header_cache={};self._chesscom_opening_mastery_cache={};self._chesscom_book_positions_cache=(None,set());self._chesscom_opening_trend_data={}
        self._chesscom_data_loaded=False

    @staticmethod
    def _configure_chesscom_table(table):
        table.verticalHeader().hide();table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True);table.setShowGrid(False);table.setWordWrap(True)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header=table.horizontalHeader();header.setStretchLastSection(False);header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def _start_chesscom_worker(self,operation,archive_url="",opponents=None,
                               known_game_ids=None,minimum_archive_month=""):
        try:username=normalize_chesscom_username(self.chesscom_username.text())
        except ValueError as exc:self.chesscom_status.setText(str(exc));return
        self.store.set_setting("chesscom_username",username)
        self.chesscom_username.setEnabled(False);self.chesscom_connect.setEnabled(False);self.chesscom_download_all.setEnabled(False)
        self.chesscom_update_downloads.setEnabled(False)
        self.chesscom_month.setEnabled(False);self.chesscom_available.setEnabled(False)
        self.chesscom_download_selected.setEnabled(False);self.chesscom_download_month.setEnabled(False)
        self.chesscom_country_lookup.setEnabled(False)
        worker=ChessComFetchWorker(operation,username,archive_url,opponents,QApplication.instance(),
                                   known_game_ids=known_game_ids,minimum_archive_month=minimum_archive_month);self._chesscom_worker=worker
        worker.archives_ready.connect(self._chesscom_archives_loaded);worker.month_ready.connect(self._chesscom_month_loaded)
        worker.month_downloaded.connect(self._chesscom_month_downloaded);worker.progress.connect(self._chesscom_all_progress)
        worker.country_ready.connect(self._chesscom_country_ready);worker.countries_progress.connect(self._chesscom_country_progress)
        worker.countries_finished.connect(self._chesscom_countries_finished)
        worker.failed.connect(self._chesscom_fetch_failed)
        worker.all_finished.connect(self._chesscom_update_finished if operation=="update" else self._chesscom_all_finished)
        worker.finished.connect(self._chesscom_worker_finished);worker.finished.connect(worker.deleteLater);worker.start()
        if operation=="countries":self.chesscom_country_cancel.setVisible(True)

    def find_chesscom_archives(self):
        self._chesscom_available_games=[];self.chesscom_available.setRowCount(0);self.chesscom_month.clear()
        self.refresh_chesscom_library(self.chesscom_username.text())
        self.chesscom_status.setText("Looking up public archives…");self._start_chesscom_worker("archives")

    def _chesscom_archives_loaded(self,archives):
        self._chesscom_archives=list(archives);self.chesscom_month.blockSignals(True);self.chesscom_month.clear()
        for archive in self._chesscom_archives:
            match=re.search(r"/(\d{4})/(\d{2})/?$",str(archive))
            if match:self.chesscom_month.addItem(f"{match.group(1)}-{match.group(2)}",str(archive))
        if self.chesscom_month.count():self.chesscom_month.setCurrentIndex(self.chesscom_month.count()-1)
        self.chesscom_month.blockSignals(False)
        if not self.chesscom_month.count():self.chesscom_status.setText("No monthly archives were found for this username.");return
        self.chesscom_status.setText(f"Found {self.chesscom_month.count()} archive months. Select a month to load its games.")
        self.load_chesscom_month()

    def load_chesscom_month(self,_index=None):
        archive=self.chesscom_month.currentData()
        if archive:
            self.chesscom_status.setText("Loading games for the selected month…")
            self._start_chesscom_worker("month",archive)

    def _chesscom_month_loaded(self,games):
        self._chesscom_available_games=list(games);self.chesscom_available.setRowCount(0)
        username=self.chesscom_username.text().strip()
        for game in self._chesscom_available_games:
            if not isinstance(game,dict):continue
            white=game.get("white") if isinstance(game.get("white"),dict) else {}
            black=game.get("black") if isinstance(game.get("black"),dict) else {}
            is_white=str(white.get("username","")).casefold()==username.casefold()
            player=white if is_white else black;opponent=black if is_white else white
            result=str(game.get("pgn","")).split('"Result "',1)[-1].split('"',1)[0] if '"Result "' in str(game.get("pgn","")) else "*"
            try:date=datetime.fromtimestamp(int(game.get("end_time"))).astimezone().strftime("%Y-%m-%d %H:%M")
            except (TypeError,ValueError,OSError):date="Unknown"
            values=(date,str(opponent.get("username","Unknown")),result,str(game.get("time_control","")),
                    str(player.get("rating","—")),"Yes" if game.get("rated") else "No")
            row=self.chesscom_available.rowCount();self.chesscom_available.insertRow(row)
            for column,text in enumerate(values):self.chesscom_available.setItem(row,column,QTableWidgetItem(text))
            self.chesscom_available.item(row,0).setData(Qt.ItemDataRole.UserRole,game)
        self.chesscom_status.setText(f"Loaded {self.chesscom_available.rowCount()} games. Choose games to download, or download the whole month.")

    def _selected_available_games(self):
        return [self.chesscom_available.item(row,0).data(Qt.ItemDataRole.UserRole)
                for row in sorted({index.row() for index in self.chesscom_available.selectionModel().selectedRows()})
                if self.chesscom_available.item(row,0)]

    def _save_chesscom_games(self,games,refresh=True):
        username=normalize_chesscom_username(self.chesscom_username.text());saved=0;errors=[]
        for game in games:
            try:self.store.save_chesscom_game(username,game);saved+=1
            except Exception as exc:errors.append(str(exc))
        self._chesscom_download_errors+=len(errors)
        if refresh:self.refresh_chesscom_library(username)
        text=f"Saved {saved} game{'s' if saved!=1 else ''} in {self.store.chesscom_root(username)}."
        if errors:text+=f" {len(errors)} entry/entries were skipped: {errors[0]}"
        self.chesscom_status.setText(text)

    def download_selected_chesscom(self):
        games=self._selected_available_games()
        if not games:self.chesscom_status.setText("Select one or more games from the month above.");return
        self._save_chesscom_games(games)

    def download_month_chesscom(self):
        if not self._chesscom_available_games:self.chesscom_status.setText("Load a month first.");return
        self._save_chesscom_games(self._chesscom_available_games)

    def download_all_chesscom(self):
        try:normalize_chesscom_username(self.chesscom_username.text())
        except ValueError as exc:self.chesscom_status.setText(str(exc));return
        self._chesscom_download_errors=0
        self.chesscom_progress.setRange(0,0);self.chesscom_progress.setVisible(True)
        self.chesscom_status.setText("Downloading monthly archives one at a time…")
        self._start_chesscom_worker("all")

    def update_downloaded_chesscom(self):
        try:normalize_chesscom_username(self.chesscom_username.text())
        except ValueError as exc:self.chesscom_status.setText(str(exc));return
        if not self._chesscom_known_game_ids or not self._chesscom_latest_game_month:
            self.chesscom_status.setText("Download all months once first, then use Update downloaded games for new games.");return
        year,month=map(int,self._chesscom_latest_game_month.split("-"));month-=1
        if month==0:year-=1;month=12
        cutoff=f"{year:04d}-{month:02d}"
        self._chesscom_download_errors=0
        self.chesscom_progress.setRange(0,0);self.chesscom_progress.setVisible(True)
        self.chesscom_status.setText(f"Checking archive months from {cutoff} for new games…")
        self._start_chesscom_worker("update",known_game_ids=self._chesscom_known_game_ids,
                                    minimum_archive_month=cutoff)

    def _chesscom_month_downloaded(self,archive,games):
        try:self._save_chesscom_games(games,refresh=(self._chesscom_worker is None or self._chesscom_worker.operation!="update"))
        except Exception as exc:self.chesscom_status.setText(f"Could not save archive {archive}: {exc}")

    def _chesscom_all_progress(self,current,total):
        self.chesscom_progress.setRange(0,max(1,total));self.chesscom_progress.setValue(current)
        if self._chesscom_worker and self._chesscom_worker.operation=="update":
            self.chesscom_status.setText(f"Checking archive month {current} of {total} for new games…")
        else:self.chesscom_status.setText(f"Downloading archive month {current} of {total}…")

    def _chesscom_all_finished(self):
        text=(f"All archive months checked. {self._chesscom_download_errors} game(s) could not be saved."
              if self._chesscom_download_errors else "All available public games have been downloaded.")
        self.chesscom_status.setText(text);self.refresh_chesscom_library(self.chesscom_username.text())

    def _chesscom_update_finished(self):
        worker=self._chesscom_worker
        added=worker.new_games_added if worker else 0
        checked=worker.archives_checked if worker else 0
        self.refresh_chesscom_library(self.chesscom_username.text())
        text=(f"Update complete: found {added:,} new game(s) after checking {checked:,} archive month(s)."
              if added else f"Update complete: no new games found in {checked:,} archive month(s).")
        if self._chesscom_download_errors:text+=f" {self._chesscom_download_errors} game(s) could not be saved."
        self.chesscom_status.setText(text)

    def _chesscom_fetch_failed(self,message):self.chesscom_status.setText(message)

    def _chesscom_worker_finished(self):
        if self.sender() is not self._chesscom_worker:return
        self._chesscom_worker=None;self.chesscom_username.setEnabled(True);self.chesscom_connect.setEnabled(True);self.chesscom_download_all.setEnabled(True)
        self.chesscom_update_downloads.setEnabled(bool(self._chesscom_known_game_ids))
        self.chesscom_month.setEnabled(True);self.chesscom_available.setEnabled(True)
        self.chesscom_download_selected.setEnabled(True);self.chesscom_download_month.setEnabled(True);self.chesscom_country_lookup.setEnabled(True)
        self.chesscom_progress.setVisible(False)
        self.chesscom_country_cancel.setVisible(False)

    def cancel_chesscom_country_lookup(self):
        if self._chesscom_worker and self._chesscom_worker.operation=="countries":
            self._chesscom_worker.cancel();self.chesscom_status.setText("Stopping after the current public profile request…")

    def lookup_chesscom_opponent_countries(self):
        try:records=self.store.chesscom_games(self.chesscom_username.text())
        except ValueError as exc:self.chesscom_status.setText(str(exc));return
        opponents=sorted({str(game.get("opponent","")).strip() for game in records
                          if not game.get("opponent_country_checked") and game.get("opponent")
                          and str(game.get("opponent")).casefold()!="unknown"},key=str.casefold)
        if not opponents:
            self.chesscom_status.setText("Opponent countries are already up to date for the downloaded games.");return
        self._chesscom_country_total=len(opponents)
        self.chesscom_progress.setRange(0,len(opponents));self.chesscom_progress.setValue(0);self.chesscom_progress.setVisible(True)
        self.chesscom_status.setText(f"Looking up public profiles for {len(opponents):,} distinct opponents…")
        self._start_chesscom_worker("countries",opponents=opponents)

    def _chesscom_country_ready(self,opponent,country):
        try:self.store.set_chesscom_opponent_country(self.chesscom_username.text(),opponent,country)
        except ValueError:pass

    def _chesscom_country_progress(self,current,total):
        self.chesscom_progress.setRange(0,max(1,total));self.chesscom_progress.setValue(current)
        self.chesscom_status.setText(f"Looking up opponent profiles · {current:,} of {total:,}…")

    def _chesscom_countries_finished(self,completed,total):
        state="finished" if completed>=total else "stopped"
        self.chesscom_status.setText(f"Country lookup {state} · checked {completed:,} of {total:,} opponent profiles.")
        self.refresh_chesscom_library(self.chesscom_username.text())

    def _set_chesscom_shape_pattern(self,pattern):
        if hasattr(self,"chesscom_shape_chart"):
            self.chesscom_shape_chart.set_pattern(pattern)

    def _open_chesscom_tactic_example(self,row,_column):
        item=self.chesscom_tactic_examples.item(row,0)
        detail=item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(detail,dict) or not isinstance(detail.get("game"),dict):return
        self.open_saved_game({"chesscom_game":detail["game"],"start_review":True,
                              "review_ply":detail.get("ply")})

    def _open_chesscom_opening_games(self,row,_column):
        item=self.chesscom_openings.item(row,0)
        if item is None or not item.text().strip():return
        self.chesscom_search.setText(item.text().strip())
        self.chesscom_scroll.ensureWidgetVisible(self.chesscom_library)

    def _set_chesscom_opening_trend(self,row,_column):
        item=self.chesscom_openings.item(row,0)
        trend_key=item.data(Qt.ItemDataRole.UserRole) if item else None
        months=self._chesscom_opening_trend_data.get(trend_key,{})
        self.chesscom_opening_results_chart.set_values([
            (month,*values[:3]) for month,values in sorted(months.items())])
        self.chesscom_opening_accuracy_chart.set_values([
            (month,sum(values[3])/len(values[3]),len(values[3]))
            for month,values in sorted(months.items()) if values[3]])

    def _refresh_chesscom_from_filters(self,*_args):
        self._chesscom_library_page=0
        self.refresh_chesscom_library(self.chesscom_username.text())

    def _change_chesscom_library_page(self,offset):
        self._chesscom_library_page=max(0,self._chesscom_library_page+int(offset))
        self._render_chesscom_library(self._chesscom_library_matches)

    def _chesscom_castling_phases_for_games(self):
        if self._chesscom_castling_phase_by_id is None:
            phases={}
            for game in self._chesscom_all_games:
                game_id=game.get("game_id")
                if not game_id:continue
                if game.get("castling_scanned"):
                    phases[game_id]=castling_timing_label(game.get("castling_phase"),game.get("castling_ply"))
                    continue
                if not game.get("reviewed"):continue
                try:payload=json.loads(str(game.get("review_json") or "{}"))
                except (TypeError,ValueError):continue
                moves=payload.get("moves",[]) if isinstance(payload,dict) else []
                if not isinstance(moves,list):continue
                castle=next((move for move in moves if isinstance(move,dict)
                             and str(move.get("san"," ")).startswith(("O-O","0-0"))),None)
                if castle is None:phases[game_id]="Did not castle"
                else:phases[game_id]=castling_timing_label(castle.get("phase"),castle.get("ply"))
            self._chesscom_castling_phase_by_id=phases
        return self._chesscom_castling_phase_by_id

    def _filter_chesscom_library_by_castling(self,row,_column=0):
        item=self.chesscom_castling.item(row,0)
        phase=item.data(Qt.ItemDataRole.UserRole) if item else None
        if not phase:return
        self._chesscom_castling_filter=None if self._chesscom_castling_filter==phase else str(phase)
        self._chesscom_library_page=0
        if self._chesscom_castling_filter:
            phases=self._chesscom_castling_phases_for_games()
            matches=[game for game in self._chesscom_library_unfiltered_matches
                     if phases.get(game.get("game_id"))==self._chesscom_castling_filter]
        else:matches=self._chesscom_library_unfiltered_matches
        self._chesscom_library_matches=matches
        self._render_chesscom_library(matches)
        self.chesscom_scroll.ensureWidgetVisible(self.chesscom_library)

    def _render_chesscom_library(self,library_matches):
        page_size=100;page_count=max(1,(len(library_matches)+page_size-1)//page_size)
        self._chesscom_library_page=min(self._chesscom_library_page,page_count-1)
        page_start=self._chesscom_library_page*page_size
        page_games=library_matches[page_start:page_start+page_size]
        self.chesscom_library.setUpdatesEnabled(False)
        self.chesscom_library.setRowCount(0)
        for game in page_games:
            try:pgn_path=Path(game["pgn_path"]);present=pgn_path.is_file()
            except (TypeError,OSError):present=False
            values=(game["played_at"][:16].replace("T"," "),game["opponent"],game["result"],
                    game["time_control"],str(game["player_rating"] or "—"),game["opening"] or "—",
                    "Reviewed" if game["reviewed"] else "Not reviewed",game["rules"] or "chess",
                    "Saved" if present else "No PGN")
            row=self.chesscom_library.rowCount();self.chesscom_library.insertRow(row)
            for column,text in enumerate(values):self.chesscom_library.setItem(row,column,QTableWidgetItem(text))
            self.chesscom_library.item(row,0).setData(Qt.ItemDataRole.UserRole,game)
        self.chesscom_library.setUpdatesEnabled(True);self.chesscom_library.viewport().update()
        first=page_start+1 if library_matches else 0
        last=page_start+len(page_games)
        label=f"Showing {first:,}–{last:,} of {len(library_matches):,} matching games · 100 per page"
        if self._chesscom_castling_filter:
            label+=f" · Castling: {self._chesscom_castling_filter} (click its row again to clear)"
        self.chesscom_library_page_label.setText(label)
        self.chesscom_library_previous.setEnabled(self._chesscom_library_page>0)
        self.chesscom_library_next.setEnabled(self._chesscom_library_page+1<page_count)

    def quick_scan_chesscom_castling(self):
        if self._castling_scan_worker and self._castling_scan_worker.isRunning():return
        records=list(self._chesscom_castling_scan_records)
        if not records:return
        self.chesscom_castling_scan.setEnabled(False)
        self.chesscom_castling_scan_status.setText(f"Scanning castling timing in {len(records):,} PGNs…")
        self.chesscom_castling_scan_progress.setRange(0,len(records));self.chesscom_castling_scan_progress.setValue(0)
        self.chesscom_castling_scan_progress.setVisible(True)
        worker=ChessComCastlingScanWorker(records,self);self._castling_scan_worker=worker
        worker.progress.connect(self._chesscom_castling_scan_progress)
        worker.scan_done.connect(self._chesscom_castling_scan_done)
        worker.finished.connect(self._chesscom_castling_scan_worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _chesscom_castling_scan_progress(self,done,total):
        self.chesscom_castling_scan_progress.setRange(0,max(1,total))
        self.chesscom_castling_scan_progress.setValue(done)

    def _chesscom_castling_scan_done(self,results):
        scans=[];errors=0
        for game_id,phase,ply,error in results:
            if error:
                errors+=1;continue
            scans.append((game_id,phase,ply))
        try:self.store.set_chesscom_castling_scans(scans)
        except (OSError,sqlite3.Error,ValueError):errors+=len(scans);scans=[]
        scanned=len(scans)
        self._chesscom_castling_scan_summary=(
            f"Scanned {scanned:,} games without engine review"
            +(f" · {errors:,} PGNs could not be read; run again to retry" if errors else " · results saved"))

    def _chesscom_castling_scan_worker_finished(self):
        if self.sender() is not self._castling_scan_worker:return
        self._castling_scan_worker=None;self.chesscom_castling_scan_progress.setVisible(False)
        self.refresh_chesscom_library(self.chesscom_username.text())
        if self._chesscom_castling_scan_summary:
            self.chesscom_castling_scan_status.setText(self._chesscom_castling_scan_summary)

    def refresh_chesscom_library(self,username):
        if not isinstance(username,str):username=self.chesscom_username.text()
        try:games=self.store.chesscom_games(username)
        except ValueError:games=[]
        self.chesscom_library.setRowCount(0);self.chesscom_openings.setRowCount(0)
        self.chesscom_opening_results_chart.set_values([]);self.chesscom_opening_accuracy_chart.set_values([])
        self.chesscom_controls.setRowCount(0);self.chesscom_colors.setRowCount(0)
        self.chesscom_opponent_bands.setRowCount(0);self.chesscom_geography.setRowCount(0)
        self.chesscom_termination.setRowCount(0);self.chesscom_calendar.setRowCount(0)
        self.chesscom_piece_accuracy.setRowCount(0);self.chesscom_castling.setRowCount(0);self.chesscom_game_shapes.setRowCount(0)
        self.chesscom_review_insights.setRowCount(0);self.chesscom_tactic_examples.setRowCount(0);self.chesscom_shape_chart.set_profiles({})
        self._chesscom_opening_trend_data={}
        available=list(games)
        self._chesscom_all_games=available;self._chesscom_castling_phase_by_id=None
        self._chesscom_known_game_ids={str(game.get("game_id")) for game in available if game.get("game_id")}
        valid_months=[str(game.get("played_at",""))[:7] for game in available
                      if re.fullmatch(r"\d{4}-\d{2}",str(game.get("played_at",""))[:7])]
        self._chesscom_latest_game_month=max(valid_months,default="")
        running=bool(self._chesscom_worker and self._chesscom_worker.isRunning())
        self.chesscom_update_downloads.setEnabled(bool(self._chesscom_known_game_ids) and not running)
        speed_choice=self.chesscom_speed_filter.currentText()
        now=datetime.now().astimezone();period_choice=self.chesscom_date_filter.currentText()
        def in_selected_period(game):
            if period_choice=="All time":return True
            try:played=datetime.fromisoformat(str(game.get("played_at","")))
            except (TypeError,ValueError):return False
            if played.tzinfo is None:played=played.astimezone()
            if period_choice=="This year":return played.year==now.year
            days={"Last 7 days":7,"Last 30 days":30,"Last 90 days":90,"Last 365 days":365}.get(period_choice)
            return days is None or played>=now-timedelta(days=days)
        selected_games=[game for game in available
                        if (speed_choice=="All time controls" or str(game.get("time_class","")).casefold()==speed_choice.casefold())
                        and in_selected_period(game)]
        self._chesscom_castling_scan_records=[game for game in selected_games
            if not game.get("castling_scanned") and game.get("rules") in ("chess","")
            and game.get("pgn_path") and Path(game["pgn_path"]).is_file()]
        scan_running=bool(self._castling_scan_worker and self._castling_scan_worker.isRunning())
        self.chesscom_castling_scan.setEnabled(bool(self._chesscom_castling_scan_records) and not scan_running)
        if scan_running:
            self.chesscom_castling_scan_status.setText("Quick scan is reading downloaded PGNs…")
        elif self._chesscom_castling_scan_records:
            self.chesscom_castling_scan_status.setText(
                f"{len(self._chesscom_castling_scan_records):,} games ready · no engine review needed")
        elif any(game.get("castling_scanned") for game in selected_games):
            self.chesscom_castling_scan_status.setText("Quick scan is up to date for these games.")
        else:
            self.chesscom_castling_scan_status.setText("Download standard-chess PGNs to scan castling timing.")
        previous_start=previous_end=None
        if period_choice in ("Last 7 days","Last 30 days","Last 90 days","Last 365 days"):
            duration=timedelta(days={"Last 7 days":7,"Last 30 days":30,"Last 90 days":90,"Last 365 days":365}[period_choice])
            previous_end=now-duration;previous_start=previous_end-duration
        elif period_choice=="This year":
            current_start=now.replace(month=1,day=1,hour=0,minute=0,second=0,microsecond=0)
            duration=now-current_start;previous_end=current_start;previous_start=previous_end-duration
        previous_games=[]
        if previous_start is not None:
            for game in available:
                if speed_choice!="All time controls" and str(game.get("time_class","")).casefold()!=speed_choice.casefold():continue
                try:played=datetime.fromisoformat(str(game.get("played_at","")))
                except (TypeError,ValueError):continue
                if played.tzinfo is None:played=played.astimezone()
                if previous_start<=played<previous_end:previous_games.append(game)
        analysis_games=list({game.get("game_id"):game for game in selected_games+previous_games}.values())
        current_stats_by_id={};current_reviews_by_id={};all_reviews_by_id={}
        for game in analysis_games:
            game_id=game.get("game_id")
            if not game.get("reviewed") or not game_id:continue
            stats_source=str(game.get("stats_json") or "{}");review_source=str(game.get("review_json") or "{}")
            cache_key=(game_id,game.get("user_color"));cached=self._chesscom_review_cache.get(cache_key)
            if cached and cached[0]==stats_source and cached[1]==review_source:
                stats_value,user_moves,all_moves=cached[2],cached[3],cached[4]
            else:
                try:stats_value=json.loads(stats_source)
                except (TypeError,ValueError):stats_value={}
                user_moves=[];all_moves=[]
                if isinstance(stats_value,dict) and stats_value.get("analysis_version")==REVIEW_ANALYSIS_VERSION:
                    try:payload=json.loads(review_source)
                    except (TypeError,ValueError):payload={}
                    if isinstance(payload,dict) and payload.get("version")==REVIEW_ANALYSIS_VERSION:
                        moves=payload.get("moves")
                        if isinstance(moves,list):
                            all_moves=[move for move in moves if isinstance(move,dict)]
                            user_moves=[move for move in all_moves if move.get("side")==game.get("user_color")]
                self._chesscom_review_cache[cache_key]=(stats_source,review_source,stats_value,user_moves,all_moves)
            if isinstance(stats_value,dict) and stats_value.get("analysis_version")==REVIEW_ANALYSIS_VERSION:
                current_stats_by_id[game_id]=stats_value
                current_reviews_by_id[game_id]=user_moves
                all_reviews_by_id[game_id]=all_moves
        library_matches=[]
        for game in available:
            query=self.chesscom_search.text().strip().casefold()
            if query and query not in str(game["opponent"]).casefold() and query not in str(game["opening"]).casefold():continue
            if speed_choice!="All time controls" and str(game["time_class"]).casefold()!=speed_choice.casefold():continue
            review_choice=self.chesscom_review_filter.currentText()
            if review_choice=="Not reviewed" and game["reviewed"]:continue
            if review_choice=="Reviewed" and not game["reviewed"]:continue
            library_matches.append(game)
        self._chesscom_library_unfiltered_matches=library_matches
        if self._chesscom_castling_filter:
            phases=self._chesscom_castling_phases_for_games()
            library_matches=[game for game in library_matches
                             if phases.get(game.get("game_id"))==self._chesscom_castling_filter]
        self._chesscom_library_matches=library_matches
        self._render_chesscom_library(library_matches)
        rated_games=[game for game in selected_games if game.get("rated") and game.get("player_rating")]
        speed_counts=Counter(str(game["time_class"]).casefold() for game in rated_games if game.get("time_class"))
        chart_speed=(speed_choice.casefold() if speed_choice!="All time controls" else
                     speed_counts.most_common(1)[0][0] if speed_counts else "")
        chart_games=([game for game in rated_games if str(game["time_class"]).casefold()==chart_speed]
                     if speed_choice=="All time controls" else rated_games)
        rating_history=[(game["played_at"],int(game["player_rating"])) for game in reversed(chart_games)]
        self.chesscom_elo_chart.set_values(rating_history)
        rating_scope=(f"MOST PLAYED CONTROL · {chart_speed.upper()}" if speed_choice=="All time controls" and chart_speed
                      else speed_choice.upper())
        rating_scope+=f" · {period_choice.upper()}"
        if chart_games:
            change=int(chart_games[0]["player_rating"])-int(chart_games[-1]["player_rating"])
            change_text=f"{change:+d} over {len(chart_games)} games" if len(chart_games)>1 else "one rated game"
            self.chesscom_rating_label.setText(
                f"RATING HISTORY · {rating_scope} · {chart_games[0]['player_rating']} now · {change_text}")
        else:self.chesscom_rating_label.setText(f"RATING HISTORY · {rating_scope or 'NO RATED GAMES'} · NO RATED GAMES")
        outcomes={"Wins":0,"Draws":0,"Losses":0}
        for game in selected_games:
            result=game.get("user_result")
            if result==1:outcomes["Wins"]+=1
            elif result==.5:outcomes["Draws"]+=1
            elif result==0:outcomes["Losses"]+=1
        scored=outcomes["Wins"]+outcomes["Draws"]+outcomes["Losses"]
        score=(outcomes["Wins"]+.5*outcomes["Draws"])/scored*100 if scored else None
        win_rate=outcomes["Wins"]/scored*100 if scored else None
        monthly=OrderedDict()
        for game in sorted(selected_games,key=lambda item:str(item.get("played_at",""))):
            month=str(game.get("played_at",""))[:7]
            result=game.get("user_result")
            if len(month)!=7 or result not in (0,.5,1):continue
            bucket=monthly.setdefault(month,[0,0,0])
            bucket[0 if result==1 else 1 if result==.5 else 2]+=1
        self.chesscom_results_chart.set_values([(month,*counts) for month,counts in monthly.items()][-18:])
        monthly_accuracy=OrderedDict();monthly_native_accuracy=OrderedDict();monthly_quality=OrderedDict()
        move_accuracy=OrderedDict()
        opening_accuracy_by_id={}
        quality_groups={"Brilliant":0,"Great":0,"Best":1,"Excellent":2,"Good":2,
                        "Miss":3,"Inaccuracy":3,"Mistake":3,"Blunder":3,"Book":4,"Forced":4}
        for game in selected_games:
            game_id=game.get("game_id")
            if game.get("chesscom_accuracy") is not None:
                try:
                    native_value=float(game["chesscom_accuracy"])
                    if 0<=native_value<=100:
                        month=str(game.get("played_at",""))[:7]
                        if len(month)==7:monthly_native_accuracy.setdefault(month,[]).append(native_value)
                except (TypeError,ValueError):pass
            if game_id not in current_stats_by_id:continue
            month=str(game.get("played_at",""))[:7]
            bucket=monthly_quality.setdefault(month,[0,0,0,0,0,0]) if len(month)==7 else None
            if bucket is not None:
                for move in current_reviews_by_id.get(game_id,[]):
                    band=quality_groups.get(str(move.get("verdict","")))
                    if band is None:continue
                    bucket[band]+=1;bucket[5]+=1
            if game.get("user_accuracy") is not None:
                if len(month)==7:monthly_accuracy.setdefault(month,[]).append(float(game["user_accuracy"]))
            move_groups={};opening_moves=[]
            for move in current_reviews_by_id.get(game_id,[]):
                try:move_number=int(move.get("move_number") or (int(move["ply"])+1)//2);accuracy=float(move["accuracy"])
                except (KeyError,TypeError,ValueError):continue
                move_groups.setdefault(move_number,[]).append(accuracy)
                if move.get("phase")=="Opening":opening_moves.append(accuracy)
            if opening_moves:opening_accuracy_by_id[game_id]=sum(opening_moves)/len(opening_moves)
            for move_number,values in move_groups.items():move_accuracy.setdefault(move_number,[]).append(sum(values)/len(values))
        self.chesscom_accuracy_chart.set_values([(month,sum(values)/len(values),len(values))
            for month,values in sorted(monthly_accuracy.items())[-18:]])
        self.chesscom_native_accuracy_chart.set_values([(month,sum(values)/len(values),len(values))
            for month,values in sorted(monthly_native_accuracy.items())[-18:]])
        quality_values=[]
        for month,counts in sorted(monthly_quality.items())[-18:]:
            total=counts[5]
            if total:quality_values.append((month,tuple(count/total*100 for count in counts[:5]),total))
        self.chesscom_quality_trend.set_values(quality_values)
        self.chesscom_move_accuracy_chart.set_values([(f"Move {number}",sum(values)/len(values),len(values))
            for number,values in sorted(move_accuracy.items())])
        opening_depth_by_id={}
        openings=getattr(self.parent(),"openings",[])
        if not openings:
            self.chesscom_opening_mastery.setText("Load the Lichess opening database to estimate how many book moves you play before leaving a line.")
        else:
            opening_key=id(openings)
            if self._chesscom_book_positions_cache[0]!=opening_key:
                self._chesscom_book_positions_cache=(opening_key,
                    {key for opening in openings for key in opening.position_keys})
            book_positions=self._chesscom_book_positions_cache[1]
            mastery_depths=[]
            if book_positions:
                for game in selected_games:
                    if game.get("rules","chess")!="chess":continue
                    path=str(game.get("pgn_path") or "")
                    if not path:continue
                    try:
                        stat=Path(path).stat();cache_key=(path,stat.st_mtime_ns,stat.st_size,opening_key)
                    except OSError:continue
                    if cache_key not in self._chesscom_opening_mastery_cache:
                        self._chesscom_opening_mastery_cache[cache_key]=local_opening_exit_depth(
                            path,game.get("user_color"),book_positions)
                    depth=self._chesscom_opening_mastery_cache[cache_key]
                    if depth is not None:
                        mastery_depths.append(depth)
                        opening_depth_by_id[game.get("game_id")]=depth
            if mastery_depths:
                average_depth=sum(mastery_depths)/len(mastery_depths)
                self.chesscom_opening_mastery.setText(
                    f"Local opening mastery estimate: you were first to leave the Lichess opening lines in "
                    f"{len(mastery_depths):,} games, after an average of {average_depth:.1f} of your book moves. "
                    "This uses the loaded Lichess database; Chess.com’s own book may differ.")
            elif book_positions:
                self.chesscom_opening_mastery.setText("No games in this period had a detected first exit by your move in the loaded Lichess opening lines.")
            else:
                self.chesscom_opening_mastery.setText("The loaded opening database has no usable lines for a mastery estimate.")
        opening_counts=Counter(game["opening"] for game in selected_games
                               if game.get("opening") and game["opening"]!="Unknown opening")
        favorite=opening_counts.most_common(1)
        opening_results={}
        for game in selected_games:
            opening=game.get("opening")
            if not opening or opening=="Unknown opening":continue
            color=game.get("user_color") or "Unknown"
            data=opening_results.setdefault((opening,color),{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0,"accuracy":[],"book_depths":[],"months":{}})
            data["games"]+=1
            opening_accuracy=opening_accuracy_by_id.get(game.get("game_id"))
            if opening_accuracy is not None:data["accuracy"].append(opening_accuracy)
            book_depth=opening_depth_by_id.get(game.get("game_id"))
            if book_depth is not None:data["book_depths"].append(book_depth)
            month=str(game.get("played_at",""))[:7]
            month_data=data["months"].setdefault(month,[0,0,0,[]]) if len(month)==7 else None
            if month_data is not None and opening_accuracy is not None:month_data[3].append(opening_accuracy)
            if game.get("user_result") is not None:
                data["scored"]+=1;data["points"]+=game["user_result"]
                data["wins"]+=game["user_result"]==1;data["draws"]+=game["user_result"]==.5;data["losses"]+=game["user_result"]==0
                if month_data is not None:
                    result_index=0 if game["user_result"]==1 else 1 if game["user_result"]==.5 else 2 if game["user_result"]==0 else None
                    if result_index is not None:month_data[result_index]+=1
        opening_rows=[]
        for color in ("White","Black","Unknown"):
            color_rows=sorted(((key,data) for key,data in opening_results.items() if key[1]==color),
                              key=lambda item:(-item[1]["games"],item[0][0]))
            opening_rows.extend(color_rows)
        for (opening,color),data in opening_rows:
            row=self.chesscom_openings.rowCount();self.chesscom_openings.insertRow(row)
            opening_score_text=f"{100*data['points']/data['scored']:.0f}%" if data["scored"] else "—"
            accuracy=f"{sum(data['accuracy'])/len(data['accuracy']):.0f}%" if data["accuracy"] else "—"
            book_depth=(f"{sum(data['book_depths'])/len(data['book_depths']):.1f} ({len(data['book_depths'])} games)"
                        if data["book_depths"] else "—")
            for column,text in enumerate((opening,color,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",opening_score_text,accuracy,book_depth)):
                item=QTableWidgetItem(text)
                if column==5:item.setToolTip("Average accuracy on your opening-phase moves in current local Stockfish reviews.")
                if column==6:item.setToolTip("Average of your book moves in games where you were first to leave the loaded Lichess opening lines.")
                if column==0:
                    trend_key=f"{color}\t{opening}"
                    self._chesscom_opening_trend_data[trend_key]=data["months"]
                    item.setData(Qt.ItemDataRole.UserRole,trend_key)
                self.chesscom_openings.setItem(row,column,item)
        if self.chesscom_openings.rowCount():self._set_chesscom_opening_trend(0,0)
        rating_estimate=predicted_performance_elo(chart_games)
        result_games=sum(game.get("rated") and game.get("opponent_rating") is not None
                         and game.get("user_result") is not None for game in chart_games)
        reviewed_stats=[];reviewed_records=[]
        for game in selected_games:
            value=current_stats_by_id.get(game.get("game_id"))
            if value is not None:reviewed_stats.append(value);reviewed_records.append((game,value))
        accuracy_values=[float(game["user_accuracy"]) for game,_stats in reviewed_records
                         if game.get("user_accuracy") is not None]
        average_accuracy=sum(accuracy_values)/len(accuracy_values) if accuracy_values else None
        native_accuracy_values=[]
        for game in selected_games:
            try:
                value=float(game.get("chesscom_accuracy"))
                if 0<=value<=100:native_accuracy_values.append(value)
            except (TypeError,ValueError):pass
        average_native_accuracy=(sum(native_accuracy_values)/len(native_accuracy_values)
                                 if native_accuracy_values else None)
        if period_choice=="All time":
            self.chesscom_period_comparison.setText("Select Last 7, 30, 90, or 365 days, or This year, to compare this period with the previous one.")
        elif not previous_games:
            self.chesscom_period_comparison.setText(f"{period_choice}: {len(selected_games):,} games. No downloaded games were found in the preceding period.")
        else:
            prior_results=[game.get("user_result") for game in previous_games if game.get("user_result") in (0,.5,1)]
            prior_score=(sum(prior_results)/len(prior_results)*100) if prior_results else None
            prior_accuracy=[float(game["user_accuracy"]) for game in previous_games
                            if game.get("game_id") in current_stats_by_id and game.get("user_accuracy") is not None]
            prior_average_accuracy=sum(prior_accuracy)/len(prior_accuracy) if prior_accuracy else None
            prior_native_accuracy=[]
            for game in previous_games:
                try:
                    value=float(game.get("chesscom_accuracy"))
                    if 0<=value<=100:prior_native_accuracy.append(value)
                except (TypeError,ValueError):pass
            prior_average_native=(sum(prior_native_accuracy)/len(prior_native_accuracy)
                                  if prior_native_accuracy else None)
            score_change=f"{score-prior_score:+.1f} pp" if score is not None and prior_score is not None else "—"
            accuracy_change=(f"{average_accuracy-prior_average_accuracy:+.1f} pp"
                             if average_accuracy is not None and prior_average_accuracy is not None else "—")
            score_comparison=f"{score:.1f}% vs {prior_score:.1f}% ({score_change})" if score is not None and prior_score is not None else "score unavailable"
            accuracy_comparison=(f"{average_accuracy:.1f}% vs {prior_average_accuracy:.1f}% ({accuracy_change})"
                                 if average_accuracy is not None and prior_average_accuracy is not None else "accuracy unavailable")
            native_accuracy_comparison=(f"Chess.com accuracy {average_native_accuracy:.1f}% vs {prior_average_native:.1f}%"
                                        if average_native_accuracy is not None and prior_average_native is not None
                                        else "Chess.com accuracy unavailable")
            self.chesscom_period_comparison.setText(
                f"{period_choice}: score {score_comparison}; local review accuracy {accuracy_comparison}; "
                f"{native_accuracy_comparison}; "
                f"{len(selected_games):,} games vs {len(previous_games):,} games in the preceding period.")
        opponent_ratings=[]
        for game in selected_games:
            try:opponent_rating=int(game.get("opponent_rating"))
            except (TypeError,ValueError):continue
            if 100<=opponent_rating<=4000:opponent_ratings.append(opponent_rating)
        average_opponent=sum(opponent_ratings)/len(opponent_ratings) if opponent_ratings else None
        best_win=max((game for game in selected_games if game.get("user_result")==1 and game.get("opponent_rating")),
                     key=lambda game:int(game["opponent_rating"]),default=None)
        current_streak=longest_streak=0
        for game in sorted(selected_games,key=lambda item:(str(item.get("played_at","")),str(item.get("game_id","")))):
            if game.get("user_result")==1:
                current_streak+=1;longest_streak=max(longest_streak,current_streak)
            else:current_streak=0
        metric_values={
            "games":(f"{len(selected_games):,}",f"of {len(available):,} downloaded · {rating_scope.lower()}"),
            "score":(f"{score:.0f}%" if score is not None else "—",f"{outcomes['Wins']} wins · {outcomes['Draws']} draws · {outcomes['Losses']} losses"),
            "win_rate":(f"{win_rate:.0f}%" if win_rate is not None else "—",f"{scored:,} completed games with a result"),
            "chesscom_accuracy":(f"{average_native_accuracy:.1f}%" if average_native_accuracy is not None else "—",
                                 f"{len(native_accuracy_values):,} games with Chess.com accuracy data"),
            "accuracy":(f"{average_accuracy:.1f}%" if average_accuracy is not None else "—",
                        f"{len(accuracy_values):,} games with current local engine reviews"),
            "reviewed":(f"{len(reviewed_records):,} / {len(selected_games):,}","with current move-by-move analysis"),
            "rating":(f"{int(chart_games[0]['player_rating']):,} Elo" if chart_games else "—",
                      f"{change_text} · {rating_scope.lower()}" if chart_games else "No rated games in this filter"),
            "opponent":(f"{average_opponent:.0f} Elo" if average_opponent is not None else "—",
                        f"Across {len(opponent_ratings):,} games with opponent ratings"),
            "best_win":(f"{int(best_win['opponent_rating']):,} Elo" if best_win else "—",
                        f"vs {best_win['opponent']}" if best_win else "No rated win in this period"),
            "streak":(f"{longest_streak} games" if longest_streak else "—","Longest consecutive win run in this period"),
        }
        for key,(value,note) in metric_values.items():
            self.chesscom_metric_values[key].setText(value);self.chesscom_metric_notes[key].setText(note)

        control_results={}
        for game in selected_games:
            label=str(game.get("time_class") or "Other").title()
            data=control_results.setdefault(label,{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0,"accuracy":[]})
            data["games"]+=1
            result=game.get("user_result")
            if result in (0,.5,1):
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
            if game.get("game_id") in current_stats_by_id and game.get("user_accuracy") is not None:
                try:data["accuracy"].append(float(game["user_accuracy"]))
                except (TypeError,ValueError):pass
        for label,data in sorted(control_results.items(),key=lambda item:(-item[1]["games"],item[0])):
            score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
            accuracy_text=f"{sum(data['accuracy'])/len(data['accuracy']):.1f}%" if data["accuracy"] else "—"
            values=(label,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",score_text,accuracy_text)
            row=self.chesscom_controls.rowCount();self.chesscom_controls.insertRow(row)
            for column,text in enumerate(values):self.chesscom_controls.setItem(row,column,QTableWidgetItem(text))

        def fill_result_breakdown(table,category_for,category_order):
            groups={label:{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0} for label in category_order}
            for game in selected_games:
                label=category_for(game);data=groups.setdefault(label,{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0})
                data["games"]+=1;result=game.get("user_result")
                if result not in (0,.5,1):continue
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
            for label,data in groups.items():
                if not data["games"]:continue
                score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
                values=(label,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",score_text)
                row=table.rowCount();table.insertRow(row)
                for column,text in enumerate(values):table.setItem(row,column,QTableWidgetItem(text))
        fill_result_breakdown(self.chesscom_colors,lambda game:game.get("user_color") or "Unknown",("White","Black"))
        def opponent_band(game):
            try:rating=int(game.get("opponent_rating"))
            except (TypeError,ValueError):return "Unknown rating"
            return "Under 1000" if rating<1000 else "1000–1499" if rating<1500 else "1500–1999" if rating<2000 else "2000+"
        rating_results={label:{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0,"expected":0.0,"rated_scored":0.0,"rated_games":0}
                        for label in ("Under 1000","1000–1499","1500–1999","2000+","Unknown rating")}
        for game in selected_games:
            label=opponent_band(game);data=rating_results[label];data["games"]+=1;result=game.get("user_result")
            if result in (0,.5,1):
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
                try:player_rating=int(game.get("player_rating"));opponent_rating=int(game.get("opponent_rating"))
                except (TypeError,ValueError):continue
                if 100<=player_rating<=4000 and 100<=opponent_rating<=4000:
                    data["rated_games"]+=1;data["rated_scored"]+=result
                    data["expected"]+=1/(1+10**max(-10,min(10,(opponent_rating-player_rating)/400)))
        for label,data in rating_results.items():
            if not data["games"]:continue
            actual=f"{data['points']/data['scored']*100:.1f}%" if data["scored"] else "—"
            expected=f"{data['expected']/data['rated_games']*100:.1f}%" if data["rated_games"] else "—"
            difference=(f"{(data['rated_scored']/data['rated_games']-data['expected']/data['rated_games'])*100:+.1f} pp"
                        if data["rated_games"] else "—")
            values=(label,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",actual,expected,difference)
            row=self.chesscom_opponent_bands.rowCount();self.chesscom_opponent_bands.insertRow(row)
            for column,text in enumerate(values):self.chesscom_opponent_bands.setItem(row,column,QTableWidgetItem(text))

        country_results={}
        for game in selected_games:
            country=str(game.get("opponent_country") or "Unknown country")
            data=country_results.setdefault(country,{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0,"accuracy":[]})
            data["games"]+=1;result=game.get("user_result")
            if result in (0,.5,1):
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
            if game.get("game_id") in current_stats_by_id and game.get("user_accuracy") is not None:
                try:data["accuracy"].append(float(game["user_accuracy"]))
                except (TypeError,ValueError):pass
        for country,data in sorted(country_results.items(),key=lambda item:(item[0]=="Unknown country",-item[1]["games"],item[0])):
            score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
            accuracy_text=f"{sum(data['accuracy'])/len(data['accuracy']):.1f}%" if data["accuracy"] else "—"
            cells=(country,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",score_text,accuracy_text)
            row=self.chesscom_geography.rowCount();self.chesscom_geography.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_geography.setItem(row,column,QTableWidgetItem(text))

        termination_groups={}
        for game in selected_games:
            path=str(game.get("pgn_path") or "")
            raw_termination=self._chesscom_pgn_header_cache.get(path)
            if raw_termination is None:
                raw_termination=""
                if path:
                    try:
                        with Path(path).open("r",encoding="utf-8-sig") as source:
                            raw_termination=str(chess.pgn.read_headers(source).get("Termination", ""))
                    except (OSError,UnicodeError,ValueError):pass
                self._chesscom_pgn_header_cache[path]=raw_termination
            phrase=raw_termination.casefold()
            reason=("Timeout vs insufficient material" if "timeout" in phrase and "insufficient" in phrase else
                    "Checkmate" if "checkmate" in phrase else
                    "Resignation" if "resign" in phrase else
                    "Timeout" if any(word in phrase for word in ("timeout","time forfeit","on time","time ran out")) else
                    "Abandonment" if "abandon" in phrase else
                    "Repetition" if "repetition" in phrase else
                    "Stalemate" if "stalemate" in phrase else
                    "Insufficient material" if "insufficient" in phrase else
                    "Agreement" if "agreement" in phrase else
                    "Move-count rule" if any(word in phrase for word in ("50-move","75-move","fifty-move","seventy-five","move rule")) else
                    "Unspecified")
            result=game.get("user_result")
            outcome="Win" if result==1 else "Draw" if result==.5 else "Loss" if result==0 else "Unknown result"
            label=f"{outcome} · {reason}"
            data=termination_groups.setdefault(label,{"games":0,"wins":0,"draws":0,"losses":0})
            data["games"]+=1;data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
        for label,data in sorted(termination_groups.items(),key=lambda item:(-item[1]["games"],item[0])):
            share=f"{data['games']/len(selected_games)*100:.1f}%" if selected_games else "—"
            cells=(label,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",share)
            row=self.chesscom_termination.rowCount();self.chesscom_termination.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_termination.setItem(row,column,QTableWidgetItem(text))

        calendar_groups={}
        calendar_order=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday",
                        "00:00–05:59","06:00–11:59","12:00–17:59","18:00–23:59"]
        for label in calendar_order:calendar_groups[label]={"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0,"accuracy":[]}
        for game in selected_games:
            try:played=datetime.fromisoformat(str(game.get("played_at",""))).astimezone()
            except (TypeError,ValueError):continue
            hour_label="00:00–05:59" if played.hour<6 else "06:00–11:59" if played.hour<12 else "12:00–17:59" if played.hour<18 else "18:00–23:59"
            for label in (played.strftime("%A"),hour_label):
                data=calendar_groups[label];data["games"]+=1
                result=game.get("user_result")
                if result in (0,.5,1):
                    data["scored"]+=1;data["points"]+=result
                    data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
                if game.get("game_id") in current_stats_by_id and game.get("user_accuracy") is not None:
                    try:data["accuracy"].append(float(game["user_accuracy"]))
                    except (TypeError,ValueError):pass
        for label,data in calendar_groups.items():
            if not data["games"]:continue
            score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
            accuracy_text=f"{sum(data['accuracy'])/len(data['accuracy']):.1f}%" if data["accuracy"] else "—"
            values=(label,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",score_text,accuracy_text)
            row=self.chesscom_calendar.rowCount();self.chesscom_calendar.insertRow(row)
            for column,text in enumerate(values):self.chesscom_calendar.setItem(row,column,QTableWidgetItem(text))

        piece_scores={"Pawn":[],"Knight":[],"Bishop":[],"Rook":[],"Queen":[],"King":[]}
        piece_by_letter={"N":"Knight","B":"Bishop","R":"Rook","Q":"Queen","K":"King"}
        for game in selected_games:
            if game.get("game_id") not in current_stats_by_id:continue
            for move in current_reviews_by_id.get(game.get("game_id"),[]):
                try:san=str(move.get("san",""));accuracy=float(move["accuracy"])
                except (KeyError,TypeError,ValueError):continue
                piece="King" if san.startswith(("O-O","0-0")) else piece_by_letter.get(san[:1],"Pawn")
                piece_scores[piece].append(accuracy)
        for piece,values in piece_scores.items():
            if not values:continue
            cells=(piece,f"{len(values):,}",f"{sum(values)/len(values):.1f}%")
            row=self.chesscom_piece_accuracy.rowCount();self.chesscom_piece_accuracy.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_piece_accuracy.setItem(row,column,QTableWidgetItem(text))
        castle_labels=("Opening · moves 1–2","Opening · move 3","Opening · move 4","Opening · move 5",
                       "Opening · move 6","Opening · move 7+","Middlegame","Endgame","Did not castle",
                       "Opening · move unknown")
        castle_results={name:{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0}
                        for name in castle_labels}
        for game in selected_games:
            game_id=game.get("game_id")
            if game.get("castling_scanned"):
                phase=castling_timing_label(game.get("castling_phase"),game.get("castling_ply"))
            elif game_id in current_reviews_by_id:
                moves=current_reviews_by_id[game_id]
                castle=next((move for move in moves if str(move.get("san"," ")).startswith(("O-O","0-0"))),None)
                phase=castling_timing_label(castle.get("phase"),castle.get("ply")) if castle else "Did not castle"
            else:continue
            if phase not in castle_results:phase="Did not castle"
            data=castle_results[phase];data["games"]+=1;result=game.get("user_result")
            if result in (0,.5,1):
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
        for phase,data in castle_results.items():
            if not data["games"]:continue
            score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
            cells=(phase,str(data["games"]),f"{data['wins']}–{data['draws']}–{data['losses']}",score_text)
            row=self.chesscom_castling.rowCount();self.chesscom_castling.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_castling.setItem(row,column,QTableWidgetItem(text))
            self.chesscom_castling.item(row,0).setData(Qt.ItemDataRole.UserRole,phase)
            self.chesscom_castling.item(row,0).setToolTip("Click to show only games in this castling category; click again to clear the filter.")

        shape_results={}
        for game,_stats in reviewed_records:
            trace=[]
            for move in all_reviews_by_id.get(game.get("game_id"),[]):
                try:value=expected_points(int(move.get("eval_before",0)))
                except (TypeError,ValueError):continue
                trace.append(value if game.get("user_color")=="White" else 1-value)
            if len(trace)<3:continue
            start,finish=trace[0],trace[-1];swings=sum(abs(right-left)>=.20 for left,right in zip(trace,trace[1:]))
            result=game.get("user_result")
            if result==1 and start<.35 and max(trace)>.65:pattern="Comeback win"
            elif result==0 and max(trace)>.65 and min(trace)<.35:pattern="Lost winning edge"
            elif swings>=2:pattern="Several large swings"
            elif result==1 and min(trace)>=.50:pattern="Mostly controlled win"
            elif result==0 and max(trace)<=.50:pattern="Mostly controlled loss"
            elif result==.5 and max(trace)-min(trace)<=.20:pattern="Stable, balanced draw"
            else:pattern="Mixed game"
            data=shape_results.setdefault(pattern,{"games":0,"wins":0,"draws":0,"losses":0,"accuracy":[],"traces":[]})
            data["games"]+=1
            data["traces"].append(trace)
            if result==1:data["wins"]+=1
            elif result==.5:data["draws"]+=1
            elif result==0:data["losses"]+=1
            if game.get("user_accuracy") is not None:data["accuracy"].append(float(game["user_accuracy"]))
        shape_profiles={}
        for pattern,data in shape_results.items():
            traces=data["traces"];bins=[[] for _ in range(12)]
            for trace in traces:
                for index in range(12):
                    position=index*(len(trace)-1)/11;lower=int(position);upper=min(len(trace)-1,lower+1)
                    fraction=position-lower
                    bins[index].append(trace[lower]*(1-fraction)+trace[upper]*fraction)
            if traces:
                shape_profiles[pattern]=(tuple(sum(values)/len(values) for values in bins),len(traces))
        self.chesscom_shape_chart.set_profiles(shape_profiles)
        current_pattern=self.chesscom_shape_pattern.currentText()
        ordered_patterns=sorted(shape_results,key=lambda pattern:(-shape_results[pattern]["games"],pattern))
        self.chesscom_shape_pattern.blockSignals(True);self.chesscom_shape_pattern.clear()
        self.chesscom_shape_pattern.addItems(ordered_patterns)
        if current_pattern in shape_profiles:self.chesscom_shape_pattern.setCurrentText(current_pattern)
        self.chesscom_shape_pattern.blockSignals(False)
        self.chesscom_shape_chart.set_pattern(self.chesscom_shape_pattern.currentText())
        shape_game_total=sum(data["games"] for data in shape_results.values())
        for pattern,data in sorted(shape_results.items(),key=lambda item:(-item[1]["games"],item[0])):
            accuracy_text=f"{sum(data['accuracy'])/len(data['accuracy']):.1f}%" if data["accuracy"] else "—"
            share=f"{data['games']/shape_game_total*100:.1f}%" if shape_game_total else "—"
            cells=(pattern,str(data["games"]),share,f"{data['wins']}–{data['draws']}–{data['losses']}",accuracy_text)
            row=self.chesscom_game_shapes.rowCount();self.chesscom_game_shapes.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_game_shapes.setItem(row,column,QTableWidgetItem(text))

        phase_values={phase:[] for phase in ("Opening","Middlegame","Endgame")};move_counts=Counter()
        for _game,stats in reviewed_records:
            for phase,value in stats.get("phase_accuracy",{}).items():
                try:phase_values.setdefault(phase,[]).append(float(value))
                except (TypeError,ValueError):pass
            if isinstance(stats.get("move_counts"),dict):
                for name,count in stats["move_counts"].items():
                    try:move_counts[name]+=max(0,int(count))
                    except (TypeError,ValueError):pass
        for phase,values in phase_values.items():
            if not values:continue
            cells=(f"{phase} accuracy",f"{len(values)} games",f"{sum(values)/len(values):.1f}%","—")
            row=self.chesscom_review_insights.rowCount();self.chesscom_review_insights.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_review_insights.setItem(row,column,QTableWidgetItem(text))
        phase_results={phase:{"games":0,"wins":0,"draws":0,"losses":0,"points":0.0,"scored":0}
                       for phase in ("Opening","Middlegame","Endgame")}
        for game,_stats in reviewed_records:
            moves=all_reviews_by_id.get(game.get("game_id"),[])
            last_move=max(moves,key=lambda move:int(move.get("ply",0)),default=None)
            phase=last_move.get("phase") if last_move else None
            if phase not in phase_results:continue
            data=phase_results[phase];data["games"]+=1;result=game.get("user_result")
            if result in (0,.5,1):
                data["scored"]+=1;data["points"]+=result
                data["wins"]+=result==1;data["draws"]+=result==.5;data["losses"]+=result==0
        for phase,data in phase_results.items():
            if not data["games"]:continue
            score_text=f"{data['points']/data['scored']*100:.0f}%" if data["scored"] else "—"
            wdl=f"{data['wins']}–{data['draws']}–{data['losses']}"
            cells=(f"Finished in {phase}",f"{data['games']} games",score_text,wdl)
            row=self.chesscom_review_insights.rowCount();self.chesscom_review_insights.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_review_insights.setItem(row,column,QTableWidgetItem(text))
        tactical_counts=Counter();tactical_games={};tactical_examples=[]
        for game,_stats in reviewed_records:
            game_id=game.get("game_id")
            user_color=game.get("user_color")
            for move in all_reviews_by_id.get(game_id,[]):
                for event in move.get("events",[]):
                    if not isinstance(event,dict):continue
                    kind=str(event.get("kind", ""));severity=str(event.get("severity", ""))
                    text=str(event.get("text", "")).casefold();label=""
                    user_move=move.get("side")==user_color
                    if kind=="fork" and severity=="positive" and user_move:label="Found · forks"
                    elif kind=="missed_tactic" and "fork" in text and user_move:label="Missed · forks"
                    elif kind=="new_pin" and severity=="positive" and user_move:label="Found · pins"
                    elif kind=="missed_tactic" and "pin" in text and user_move:label="Missed · pins"
                    elif kind=="checkmate" and severity=="positive" and user_move:label="Found · checkmates"
                    elif kind=="missed_mate" and user_move:label="Missed · forced mates"
                    elif kind=="opponent_hanging_piece" and severity=="positive" and user_move:label="Found · opponent pieces left hanging"
                    elif kind in ("hanging_piece","lost_defender") and severity=="critical":
                        label="Allowed · your pieces left hanging" if user_move else "Allowed · opponent pieces left hanging"
                    elif kind in ("opponent_mate","back_rank_mate") and severity=="critical" and user_move:label="Allowed · checkmates"
                    elif kind=="checkmate" and severity=="positive" and not user_move:label="Opponent found · checkmates"
                    if not label:continue
                    tactical_counts[label]+=1
                    tactical_games.setdefault(label,set()).add(game_id)
                    try:ply=int(move.get("ply",0))
                    except (TypeError,ValueError):ply=0
                    tactical_examples.append((str(game.get("played_at","")),ply,label,game,
                                              move.get("side",""),move.get("san",""),event.get("text","")))
        for label,count in sorted(tactical_counts.items(),key=lambda item:(-item[1],item[0])):
            cells=(label,f"{len(tactical_games[label])} games",f"{count} occurrences","—")
            row=self.chesscom_review_insights.rowCount();self.chesscom_review_insights.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_review_insights.setItem(row,column,QTableWidgetItem(text))
        tactical_examples.sort(key=lambda item:(item[0],item[1]),reverse=True)
        for played_at,ply,label,game,side,san,detail in tactical_examples[:30]:
            try:date=played_at[:16].replace("T"," ")
            except (TypeError,AttributeError):date=""
            move_label=f"{(ply+1)//2}{'.' if side=='White' else '...'} {san}" if ply else str(san)
            row=self.chesscom_tactic_examples.rowCount();self.chesscom_tactic_examples.insertRow(row)
            values=(label,date,str(side),move_label,f"{game.get('opponent','Unknown')} · {detail}")
            for column,value in enumerate(values):
                item=QTableWidgetItem(str(value));self.chesscom_tactic_examples.setItem(row,column,item)
            self.chesscom_tactic_examples.item(row,0).setData(Qt.ItemDataRole.UserRole,
                                                               {"game":game,"ply":ply})
        total_classified=sum(move_counts.values())
        for label,count in sorted(move_counts.items(),key=lambda item:(-item[1],item[0])):
            if count<=0:continue
            share=f"{count/total_classified*100:.1f}%" if total_classified else "—"
            cells=(label,f"{count:,} moves","Across current reviews",share)
            row=self.chesscom_review_insights.rowCount();self.chesscom_review_insights.insertRow(row)
            for column,text in enumerate(cells):self.chesscom_review_insights.setItem(row,column,QTableWidgetItem(text))
        recurring_errors=recurring_review_themes(reviewed_stats,limit=2)
        recurring_strengths=recurring_review_strengths(reviewed_stats,limit=2)
        prediction=(f"Result-based performance estimate: ~{rating_estimate} Elo from {result_games} rated results (not an official rating)."
                    if rating_estimate else "Performance Elo estimate appears after 8 games with opponent ratings and results.")
        opening_text=f"Most played opening: {favorite[0][0]} ({favorite[0][1]} games)." if favorite else "Opening trends appear when game metadata includes an ECO/opening."
        lesson_text=("Recurring lesson: "+", ".join(f"{name} ({count} games)" for name,count in recurring_errors)+".") if recurring_errors else "Long-term coaching themes build after the same pattern appears in multiple reviewed games."
        strength_text=("Recurring strength: "+", ".join(f"{name} ({count} games)" for name,count in recurring_strengths)+".") if recurring_strengths else ""
        self.chesscom_trends.setText(
            f"Showing {len(selected_games)} of {len(available)} downloaded games · {sum(bool(game['reviewed']) for game in selected_games)} reviewed · "
            f"{outcomes['Wins']} wins / {outcomes['Draws']} draws / {outcomes['Losses']} losses. {prediction} {opening_text} {lesson_text} {strength_text}")

    def _selected_chesscom_records(self):
        return [self.chesscom_library.item(row,0).data(Qt.ItemDataRole.UserRole)
                for row in sorted({index.row() for index in self.chesscom_library.selectionModel().selectedRows()})
                if self.chesscom_library.item(row,0)]

    def open_selected_chesscom(self):
        records=self._selected_chesscom_records()
        if not records:return
        if not Path(records[0]["pgn_path"]).is_file():
            self.chesscom_status.setText("This game’s PGN was removed. Download the month again to restore it.");return
        if records[0]["rules"] not in ("chess",""):
            self.chesscom_status.setText("This game uses a variant that the current review board does not support.");return
        self.open_game_callback({"chesscom_game":records[0]});self.accept()

    def review_selected_chesscom(self):
        records=self._selected_chesscom_records()
        if not records:self.chesscom_status.setText("Select one or more downloaded games to review.");return
        supported=[game for game in records if game["rules"] in ("chess","") and game["pgn_path"] and Path(game["pgn_path"]).is_file()]
        if not supported:
            reason=("Selected games use unsupported chess variants." if all(game["rules"] not in ("chess","") for game in records)
                    else "Selected games do not have stored PGN files. Download their month again to restore them.")
            self.chesscom_status.setText(reason);return
        if len(supported)!=len(records):self.chesscom_status.setText(f"Skipping {len(records)-len(supported)} game(s) without a stored standard-chess PGN.")
        self.open_game_callback({"chesscom_batch":supported});self.accept()

    def review_unreviewed_chesscom(self):
        try:rows=self.store.chesscom_games(self.chesscom_username.text())
        except ValueError:rows=[]
        records=[game for game in rows if not game["reviewed"] and game["rules"] in ("chess","")
                 and game["pgn_path"] and Path(game["pgn_path"]).is_file()]
        if not records:self.chesscom_status.setText("There are no downloaded, unreviewed standard-chess games.");return
        self.open_game_callback({"chesscom_batch":records});self.accept()

    def delete_selected_chesscom(self):
        records=self._selected_chesscom_records()
        for game in records:self.store.remove_chesscom_game(game["game_id"])
        self.refresh_chesscom_library(self.chesscom_username.text())

    def open_chesscom_folder(self):
        try:root=self.store.chesscom_root(self.chesscom_username.text())
        except ValueError as exc:self.chesscom_status.setText(str(exc));return
        root.mkdir(parents=True,exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))):
            self.chesscom_status.setText(f"PGN folder: {root}")

    def change_theme(self,_index):
        theme_key=self.theme_choice.currentData()
        if theme_key not in BOARD_THEMES:return
        self.theme_preview.set_theme(theme_key)
        if self.theme_changed_callback:self.theme_changed_callback(theme_key)

    def change_presentation(self,*_args):
        piece_style=self.piece_choice.currentData()
        coordinates=self.coordinate_toggle.isChecked();animations=self.motion_toggle.isChecked()
        self.theme_preview.set_presentation(piece_style,coordinates,animations)
        if self.presentation_changed_callback:self.presentation_changed_callback(piece_style,coordinates,animations)

    def refresh(self):
        self.all_records=self.store.games()
        stats_by_game={}
        for game in self.all_records:
            try:stats=json.loads(game.get("stats_json") or "{}")
            except (TypeError,json.JSONDecodeError):stats={}
            stats_by_game[game["game_key"]]=stats if isinstance(stats,dict) else {}
        current_review_keys={key for key,stats in stats_by_game.items()
                             if stats.get("analysis_version")==REVIEW_ANALYSIS_VERSION}
        old_opening=self.opening_filter.currentData();self.opening_filter.blockSignals(True);self.opening_filter.clear();self.opening_filter.addItem("All openings",None)
        for opening in sorted({g["opening"] for g in self.all_records if g["opening"]}):self.opening_filter.addItem(opening,opening)
        opening_index=self.opening_filter.findData(old_opening)
        if opening_index>=0:self.opening_filter.setCurrentIndex(opening_index)
        self.opening_filter.blockSignals(False)
        self.records=self._filtered_records(self.all_records);self.table.setRowCount(len(self.records))
        for row,game in enumerate(self.records):
            current=game["game_key"] in current_review_keys
            if game["user_accuracy"] is None:acc="—"
            elif current:acc=f"{game['user_accuracy']:.0f}%"
            else:acc="Refresh" if game["pgn"] else "Older"
            opponent=("Unknown" if game["opponent_elo"] is None else f"~{game['opponent_elo']} Elo" if game["elo_approx"] else f"{game['opponent_elo']} Elo")
            if game["average_loss"] is None:avg="—"
            elif current:avg=f"{game['average_loss']:.0f} cp"
            else:avg="Refresh" if game["pgn"] else "Older"
            cells=[game["played_at"][:16].replace("T"," "),game["result"],acc,opponent,game["opening"],avg,"Saved" if game["pgn"] else "Summary only"]
            for col,text in enumerate(cells):
                item=QTableWidgetItem(text)
                if col in (2,5) and not current and (game["user_accuracy"] is not None or game["average_loss"] is not None):
                    item.setToolTip("This score uses an older move-quality model. Reanalyze the saved PGN to compare it with current reviews." if game["pgn"] else "This score uses an older move-quality model; the PGN was not saved, so it cannot be refreshed.")
                self.table.setItem(row,col,item)
        current_records=[g for g in self.records if g["game_key"] in current_review_keys]
        outdated_review_games=sum(1 for g in self.records if g["user_accuracy"] is not None and g["game_key"] not in current_review_keys)
        refreshable_old_games=sum(1 for g in self.records if g["user_accuracy"] is not None and g["game_key"] not in current_review_keys and g["pgn"])
        self.chart.set_values([g["user_accuracy"] for g in reversed(current_records) if g["user_accuracy"] is not None])
        rated=[g for g in self.all_records if g["user_result"] is not None and g["opponent_elo"] is not None and not g["elo_approx"]]
        accuracy_values=[g["user_accuracy"] for g in current_records if g["user_accuracy"] is not None]
        average=sum(accuracy_values)/len(accuracy_values) if accuracy_values else None
        wins=sum(g["user_result"]==1 for g in self.records if g["user_result"] is not None);draws=sum(g["user_result"]==.5 for g in self.records if g["user_result"] is not None);losses=sum(g["user_result"]==0 for g in self.records if g["user_result"] is not None)
        profile_stats=[];phase_sums={};current_analysis_games=0
        quality_counts={name:0 for name in ("Brilliant","Great","Miss","Blunder")}
        openings={}
        for game in current_records:
            if game["user_accuracy"] is not None and game["opening"] not in ("Opening not found in local database","Opening database not loaded"):
                openings.setdefault((game["opening"],game["user_color"]),[]).append(game["user_accuracy"])
            stats=stats_by_game[game["game_key"]]
            profile_stats.append(stats);current_analysis_games+=1
            for name,value in stats.get("phase_accuracy",{}).items():phase_sums.setdefault(name,[]).append(float(value))
            for name in quality_counts:
                quality_counts[name]+=int(stats.get("move_counts",{}).get(name,0))
        weakest=recurring_review_themes(profile_stats)
        strengths=recurring_review_strengths(profile_stats)
        phase_text=" · ".join(f"{name} {sum(vals)/len(vals):.0f}%" for name,vals in sorted(phase_sums.items()) if vals)
        repeated=[entry for entry in openings.items() if len(entry[1])>=2]
        best_opening=max(repeated,key=lambda entry:sum(entry[1])/len(entry[1]),default=None)
        opening_text=(f" · strongest repeat opening: {best_opening[0][0]} as {best_opening[0][1]} ({sum(best_opening[1])/len(best_opening[1]):.0f}%, {len(best_opening[1])} games)" if best_opening else "")
        themes=" · ".join(f"{name} in {count} games" for name,count in weakest) or "No error theme has repeated across multiple games yet"
        strength_text=" · ".join(f"{name} in {count} games" for name,count in strengths) or "No skill pattern has repeated across multiple games yet"
        average_text=f"{average:.1f}%" if average is not None else "—"
        quality_text=(f"move classifications in {current_analysis_games} current review{'s' if current_analysis_games!=1 else ''}: "
                      f"{quality_counts['Brilliant']} brilliant, {quality_counts['Great']} great, "
                      f"{quality_counts['Miss']} missed chances, {quality_counts['Blunder']} blunders"
                      if current_analysis_games else
                      "Older scores use a previous move-quality model" if outdated_review_games else
                      "Move classifications appear after your first review")
        refresh_note=(f" · {refreshable_old_games} older score(s) can be refreshed from saved PGNs" if refreshable_old_games else "")
        self.insights.setText(f"Showing {len(self.records)} of {len(self.all_records)} games · {wins} wins / {draws} draws / {losses} losses · average move quality {average_text} · {quality_text} · {phase_text or 'Phase move quality builds as more reviews are saved'} · recurring error themes: {themes} · recurring strengths: {strength_text}{opening_text}{refresh_note}")
        if len(self.records)==0: self.insights.setText("No games match these filters.")
        if len(rated)<8:
            self.rating.setText("Provisional bot estimate · not enough games")
            self.rating_note.setText(f"{len(rated)} completed games against non-approximate opponent settings. At least 8 are needed for a rough local estimate; this is not a human rating.")
        else:
            points=sum(float(g["user_result"]) for g in rated);score=max(.05,min(.95,(points+.5)/(len(rated)+1)))
            opponents=sum(int(g["opponent_elo"]) for g in rated)/len(rated);estimate=max(100,min(4000,round(opponents+400*math.log10(score/(1-score)))))
            self.rating.setText(f"Provisional bot estimate · ~{estimate}")
            self.rating_note.setText(f"Based on {len(rated)} completed games against non-approximate Stockfish settings (mean setting {opponents:.0f}). Engine levels do not map exactly to human ratings; use this as a trend, not a rating." )

    def _filtered_records(self,records):
        now=datetime.now().astimezone();selected=[]
        for game in records:
            try:played=datetime.fromisoformat(game["played_at"])
            except (ValueError,TypeError):played=None
            date_choice=self.date_filter.currentText()
            if played and played.tzinfo is None:played=played.astimezone()
            if played and date_choice=="Last 30 days" and (now-played).days>30:continue
            if played and date_choice=="Last 90 days" and (now-played).days>90:continue
            if played and date_choice=="This year" and played.year!=now.year:continue
            if self.color_filter.currentText()=="As White" and game["user_color"]!="White":continue
            if self.color_filter.currentText()=="As Black" and game["user_color"]!="Black":continue
            opening=self.opening_filter.currentData()
            if opening and game["opening"]!=opening:continue
            rating=game["opponent_elo"]
            range_choice=self.opponent_filter.currentText()
            if range_choice!="Any opponent" and rating is None:continue
            if range_choice=="Below 1000" and rating>=1000:continue
            if range_choice=="1000–1599" and not 1000<=rating<1600:continue
            if range_choice=="1600–2199" and not 1600<=rating<2200:continue
            if range_choice=="2200+" and rating<2200:continue
            result=game["user_result"]
            choice=self.result_filter.currentText()
            if choice=="Wins" and result!=1:continue
            if choice=="Draws" and result!=.5:continue
            if choice=="Losses" and result!=0:continue
            selected.append(game)
        return selected

    def open_selected(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.records):return
        pgn=self.records[row]["pgn"]
        if not pgn:
            QMessageBox.information(self,"Summary only","This game’s summary is saved, but its moves weren’t stored. Enable full PGN storage for future games, or export a PGN from the game review.");return
        self.open_game_callback(self.records[row]);self.accept()

    def delete_selected(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.records):return
        game=self.records[row]
        answer=QMessageBox.question(self,"Delete game?","Delete this game and its saved review summary?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:self.store.delete_game(game["game_key"]);self.refresh()

    def clear_all_data(self):
        answer=QMessageBox.question(self,"Delete all profile data?","Permanently delete every saved game summary and PGN from this computer?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:
            self.store.clear_all();self.refresh();self.refresh_chesscom_library(self.chesscom_username.text())

    def clear_pgn_data(self):
        answer=QMessageBox.question(self,"Delete stored game moves?","Remove all saved PGNs? Review summaries and profile statistics will remain.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.store.clear_pgns();self.refresh();self.refresh_chesscom_library(self.chesscom_username.text())

    def closeEvent(self,event):
        if self._chesscom_worker and self._chesscom_worker.isRunning():self._chesscom_worker.cancel()
        if self._castling_scan_worker and self._castling_scan_worker.isRunning():
            self._castling_scan_worker.requestInterruption();self._castling_scan_worker.wait(2000)
        super().closeEvent(event)


class ChessWindow(QMainWindow):
    engine_request = pyqtSignal(object)
    bot_request = pyqtSignal(object)
    best_request = pyqtSignal(object)
    review_request = pyqtSignal(object)
    practice_analysis_request = pyqtSignal(object)
    elo_request = pyqtSignal(int)
    shutdown_request = pyqtSignal()
    evaluation_request = pyqtSignal(object)
    openings_loaded = pyqtSignal(object, object)
    explore_request = pyqtSignal(object)
    puzzle_request = pyqtSignal(object)
    syzygy_request = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chess · Offline Studio")
        self.setMinimumSize(1100, 760); self.resize(2560, 1440)
        self.game_start_board=chess.Board();self.board = self.game_start_board.copy(); self.history: list[chess.Move] = []; self.player_color = chess.WHITE
        self.selected = None; self.last_move = None; self.best_move = None
        self.review_moves: list[ReviewMove] = []; self.reviewing = False; self.review_index = 0
        self.external_review_game_id=None;self.external_review_username=None
        self.chesscom_review_queue=[];self.chesscom_batch_total=0;self.chesscom_batch_completed=0;self.chesscom_batch_started_at=None;self.chesscom_review_wait_attempts=0
        self.review_show_best=False;self.review_show_reply=False;self.review_pv_active=False;self.review_pv_index=0;self.review_pv_kind="best"
        self.review_speech=None;self._last_spoken_ply=None
        self.review_busy = False; self.bot_busy = False; self.engine_ready = False; self.engine_startup_timed_out = False
        self.game_result_override: Optional[str] = None
        self.game_version = 0
        self.openings: list[Opening] = []; self.book_path: Optional[str] = None
        self.review_token=0; self.sound_enabled = True; self.sounds = {}; self.explorer_dialog=None
        self.review_progress_dialog=None
        self.puzzle_dialog=None;self.puzzle_token=0
        self.rating_eligible=True
        try:self.profile_store=ProfileStore();self.profile_error=None
        except Exception as exc:self.profile_store=None;self.profile_error=str(exc)
        self._saved_game_stats_cache=None
        self.syzygy_path="";self.syzygy_active=False;self.syzygy_supported=False;self.syzygy_error=""
        if self.profile_store:
            try:self.syzygy_path=self.profile_store.get_setting("syzygy_path","")
            except Exception:pass
        self.board_theme="classic_green";self.piece_style="tournament";self.show_coordinates=True;self.board_animations=True
        if self.profile_store:
            try:
                saved_theme=self.profile_store.get_setting("board_theme","classic_green")
                if saved_theme in BOARD_THEMES:self.board_theme=saved_theme
                saved_piece=self.profile_store.get_setting("piece_style","tournament")
                if saved_piece in PIECE_STYLES:self.piece_style=saved_piece
                self.show_coordinates=self.profile_store.get_setting("show_coordinates","1")!="0"
                self.board_animations=self.profile_store.get_setting("board_animations","1")!="0"
            except Exception:pass
        ChessBoard.default_theme=self.board_theme
        ChessBoard.default_piece_style=self.piece_style
        ChessBoard.default_show_coordinates=self.show_coordinates
        ChessBoard.default_animations=self.board_animations
        self._setup_ui(); self._setup_engine()
        self.openings_loaded.connect(self._set_openings)
        self.worker.position_eval.connect(self.evalbar.set_eval)
        self.evaluation_request.connect(self.worker.evaluate_position)
        self._load_default_openings(); self._sync_board()
        if IS_FROZEN and os.name == "nt":
            self._update_check_worker=None
            self._update_download_worker=None
            QTimer.singleShot(2500,self._check_for_updates)

    def _check_for_updates(self):
        if self._update_check_worker and self._update_check_worker.isRunning():return
        self._update_check_worker=UpdateCheckWorker(APP_VERSION,self)
        self._update_check_worker.update_available.connect(self._offer_update)
        self._update_check_worker.finished.connect(self._update_check_finished)
        self._update_check_worker.start()

    def _update_check_finished(self):
        worker=self._update_check_worker
        self._update_check_worker=None
        if worker:worker.deleteLater()

    def _offer_update(self,release):
        tag=release["tag_name"]
        release_url=f"https://github.com/JIMMYJAMTHEVERYFATMAN/Chess/releases/tag/{urllib.parse.quote(tag,safe='')}"
        prompt=QMessageBox(self)
        prompt.setWindowTitle("Chess update available")
        prompt.setTextFormat(Qt.TextFormat.RichText)
        prompt.setText(
            f"Version {tag} is available. Download and install it now? "
            "The app will restart when the update is ready.<br><br>"
            f'<a href="{release_url}">View what’s new in this version on GitHub</a>')
        prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        for label in prompt.findChildren(QLabel):
            if "href=" in label.text():
                label.setOpenExternalLinks(True)
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        prompt.setDefaultButton(QMessageBox.StandardButton.Yes)
        if prompt.exec()!=QMessageBox.StandardButton.Yes:return
        self.statusBar().showMessage(f"Downloading Chess {tag}…")
        self._update_download_worker=UpdateDownloadWorker(release,self)
        self._update_download_worker.progress.connect(self._update_download_progress)
        self._update_download_worker.downloaded.connect(self._install_update)
        self._update_download_worker.failed.connect(self._update_download_failed)
        self._update_download_worker.finished.connect(self._update_download_finished)
        self._update_download_worker.start()

    def _update_download_progress(self,done,total):
        if total:
            self.statusBar().showMessage(f"Downloading update… {done*100//total}%")
        else:
            self.statusBar().showMessage(f"Downloading update… {done//(1024*1024)} MB")

    def _install_update(self,downloaded_path):
        try:
            launch_windows_update(downloaded_path,Path(sys.executable).resolve())
        except Exception as exc:
            QMessageBox.critical(self,"Could not install update",str(exc))
            return
        self.statusBar().showMessage("Update downloaded. Restarting…")
        self.close()

    def _update_download_failed(self,message):
        QMessageBox.warning(self,"Update failed",message)

    def _update_download_finished(self):
        worker=self._update_download_worker
        self._update_download_worker=None
        if worker:worker.deleteLater()

    def _setup_engine(self):
        self.thread = QThread(self); self.worker = EngineWorker(self.syzygy_path); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start_engine)
        self.elo_request.connect(self.worker.set_elo); self.bot_request.connect(self.worker.play_bot)
        self.best_request.connect(self.worker.find_best); self.review_request.connect(self.worker.review_game)
        self.practice_analysis_request.connect(self.worker.analyze_practice_attempt)
        self.explore_request.connect(self.worker.explore)
        self.puzzle_request.connect(self.worker.generate_puzzle)
        self.syzygy_request.connect(self.worker.configure_syzygy)
        self.shutdown_request.connect(self.worker.shutdown)
        self.worker.ready.connect(self._engine_started); self.worker.failed.connect(self._engine_failed)
        self.worker.bot_move.connect(self._bot_move); self.worker.best_move.connect(self._best_move)
        self.worker.review_progress.connect(self._review_progress); self.worker.review_done.connect(self._review_done)
        self.worker.explore_done.connect(self._explorer_done)
        self.worker.puzzle_progress.connect(self._puzzle_progress);self.worker.puzzle_done.connect(self._puzzle_done)
        self.worker.operation_error.connect(self._engine_error)
        self.worker.syzygy_status.connect(self._syzygy_configured)
        self.thread.start()
        self.engine_startup_timer=QTimer(self);self.engine_startup_timer.setSingleShot(True)
        self.engine_startup_timer.timeout.connect(self._engine_startup_timeout)
        self.engine_startup_timer.start(30000)

    def _setup_ui(self):
        central=QWidget(); self.setCentralWidget(central); root=QVBoxLayout(central); root.setContentsMargins(32,26,32,24); root.setSpacing(20)
        top=QHBoxLayout(); root.addLayout(top)
        brand=QLabel("♞  CHESS"); brand.setObjectName("Brand"); top.addWidget(brand)
        top.addSpacing(18); self.engine_status=QLabel("Connecting to Stockfish…"); self.engine_status.setObjectName("Muted"); top.addWidget(self.engine_status)
        self.syzygy_btn=QPushButton("Tablebases · Off");self.syzygy_btn.setToolTip("Use optional local Syzygy endgame tablebases for exact results with up to seven pieces.");self.syzygy_btn.clicked.connect(self.open_syzygy_menu);top.addWidget(self.syzygy_btn);self._update_syzygy_button()
        top.addStretch()
        self.opening_label=QLabel("Opening database not loaded"); self.opening_label.setObjectName("OpeningPill"); top.addWidget(self.opening_label)
        top.addSpacing(12)
        self.openings_btn=QPushButton("Load Lichess DB"); self.openings_btn.clicked.connect(self.load_opening_dialog); top.addWidget(self.openings_btn)
        self.book_btn=QPushButton("Load weighted book"); self.book_btn.clicked.connect(self.load_book_dialog); top.addWidget(self.book_btn)
        self.profile_btn=QPushButton("Profile"); self.profile_btn.clicked.connect(self.open_profile); top.addWidget(self.profile_btn)
        self.puzzles_btn=QPushButton("Puzzles");self.puzzles_btn.clicked.connect(self.open_puzzles);top.addWidget(self.puzzles_btn)
        self.fullscreen_btn=QPushButton("Exit fullscreen");self.fullscreen_btn.setToolTip("F11 toggles full screen · Esc returns to windowed mode");self.fullscreen_btn.clicked.connect(self.toggle_fullscreen);top.addWidget(self.fullscreen_btn)

        splitter=QSplitter(Qt.Orientation.Horizontal); root.addWidget(splitter,1); splitter.setChildrenCollapsible(False)
        play_area=QWidget(); play_col=QVBoxLayout(play_area); play_col.setContentsMargins(0,0,14,0); play_col.setSpacing(8)
        self.opponent=QFrame(); self.opponent.setObjectName("PlayerCard"); opp_row=QHBoxLayout(self.opponent);opp_row.setContentsMargins(20,14,20,14)
        self.opponent_avatar=QLabel("♟"); self.opponent_avatar.setObjectName("Avatar"); opp_row.addWidget(self.opponent_avatar)
        opp_info=QVBoxLayout(); self.opponent_name=QLabel("Stockfish 19"); self.opponent_name.setObjectName("PlayerName"); self.opponent_rating=QLabel("Opponent · 1320 Elo"); self.opponent_rating.setObjectName("Muted"); opp_info.addWidget(self.opponent_name); opp_info.addWidget(self.opponent_rating); opp_row.addLayout(opp_info); opp_row.addStretch()
        self.opponent_clock=QLabel("—:——");self.opponent_clock.setObjectName("ChessComClock");self.opponent_clock.setToolTip("Recorded Chess.com clock at this move");self.opponent_clock.setVisible(False);opp_row.addWidget(self.opponent_clock)
        play_col.addWidget(self.opponent)
        board_row=QHBoxLayout(); board_row.setSpacing(8); play_col.addLayout(board_row,1)
        self.evalbar=EvalBar(); board_row.addWidget(self.evalbar)
        self.board_widget=ChessBoard(); self.board_widget.square_clicked.connect(self.on_square); board_row.addWidget(self.board_widget,1)
        self.you=QFrame(); self.you.setObjectName("PlayerCard"); yourow=QHBoxLayout(self.you);yourow.setContentsMargins(20,14,20,14)
        av=QLabel("♙"); av.setObjectName("Avatar"); yourow.addWidget(av)
        youinfo=QVBoxLayout(); self.you_name=QLabel("You · White"); self.you_name.setObjectName("PlayerName"); self.turn_label=QLabel("Your turn"); self.turn_label.setObjectName("Muted"); youinfo.addWidget(self.you_name); youinfo.addWidget(self.turn_label); yourow.addLayout(youinfo); yourow.addStretch()
        self.you_clock=QLabel("—:——");self.you_clock.setObjectName("ChessComClock");self.you_clock.setToolTip("Recorded Chess.com clock at this move");self.you_clock.setVisible(False);yourow.addWidget(self.you_clock)
        play_col.addWidget(self.you)
        splitter.addWidget(play_area)

        panel=QFrame(); panel.setObjectName("SidePanel"); panel.setMinimumWidth(600); panel.setMaximumWidth(860)
        panel_col=QVBoxLayout(panel); panel_col.setContentsMargins(22,20,22,20); panel_col.setSpacing(15)
        mode_row=QHBoxLayout();mode_row.setSpacing(8);self.mode_buttons={}
        for key,label in (("play","Play"),("review","Review"),("coach","Coach")):
            button=self._button(label,"mode");button.setProperty("active",key=="play");button.clicked.connect(lambda _checked=False,chosen=key:self.set_mode(chosen));mode_row.addWidget(button,1);self.mode_buttons[key]=button
        panel_col.addLayout(mode_row)
        self.mode_stack=QStackedWidget();panel_col.addWidget(self.mode_stack,1)

        play_page=QWidget();play_col=QVBoxLayout(play_page);play_col.setContentsMargins(0,0,0,0);play_col.setSpacing(14)
        title=QLabel("LIVE GAME");title.setObjectName("SectionTitle");play_col.addWidget(title)
        self.play_hint=QLabel("Your current game is ready. Select a move on the board, or import a PGN.");self.play_hint.setObjectName("ReviewSummary");self.play_hint.setWordWrap(True);play_col.addWidget(self.play_hint)
        move_title=QLabel("MOVE LIST");move_title.setObjectName("SectionTitle");play_col.addWidget(move_title)
        self.move_table=QTableWidget(0,3);self.move_table.setHorizontalHeaderLabels(["#","Move","Eval"]);self._configure_table(self.move_table);self.move_table.cellClicked.connect(self.jump_to_game_row)
        move_header=self.move_table.horizontalHeader();move_header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);move_header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);move_header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);play_col.addWidget(self.move_table,1)
        controls=QGridLayout();controls.setHorizontalSpacing(10);controls.setVerticalSpacing(10)
        btn_specs=[("New game",self.reset,"secondary"),("Undo",self.undo,"secondary"),("Resign",self.resign,"danger"),("Best move",self.request_best,"secondary"),("Import PGN",self.import_pgn,"secondary"),("Save PGN",self.export_pgn,"secondary")]
        for index,(label,callback,style) in enumerate(btn_specs):
            button=self._button(label,style);button.clicked.connect(callback);controls.addWidget(button,index//3,index%3)
        play_col.addLayout(controls)
        setup=QFrame();setup.setObjectName("InsetCard");setup_col=QVBoxLayout(setup);setup_col.setContentsMargins(14,14,14,14);setup_col.setSpacing(10)
        side_row=QHBoxLayout();side_label=QLabel("Play as");side_label.setObjectName("PlayerName");side_row.addWidget(side_label);side_row.addStretch();self.side_choice=QComboBox();self.side_choice.addItems(["White","Black"]);self.side_choice.currentIndexChanged.connect(self.choose_side);side_row.addWidget(self.side_choice);setup_col.addLayout(side_row)
        elo_row=QHBoxLayout();elo_title=QLabel("Opponent strength");elo_title.setObjectName("PlayerName");self.elo_value=QLabel("1320");self.elo_value.setObjectName("AccentText");elo_row.addWidget(elo_title);elo_row.addStretch();elo_row.addWidget(self.elo_value);setup_col.addLayout(elo_row)
        self.elo_slider=QSlider(Qt.Orientation.Horizontal);self.elo_slider.setRange(MIN_TARGET_ELO,MAX_ELO);self.elo_slider.setValue(MIN_ELO);self.elo_slider.setSingleStep(10);self.elo_slider.setToolTip("Below Stockfish's 1320 UCI minimum, weaker moves are selected to approximate the requested strength.");self.elo_slider.valueChanged.connect(self.change_elo);setup_col.addWidget(self.elo_slider)
        elo_marks=QHBoxLayout();elo_marks.addWidget(self._small("400 · approximate"));elo_marks.addStretch();elo_marks.addWidget(self._small("2200"));elo_marks.addStretch();elo_marks.addWidget(self._small("3190"));setup_col.addLayout(elo_marks)
        foot=QHBoxLayout();self.sound_toggle=QCheckBox("Sound effects");self.sound_toggle.setChecked(True);self.sound_toggle.toggled.connect(self.set_sound);foot.addWidget(self.sound_toggle);foot.addStretch();self.flip_btn=self._button("Flip board","secondary");self.flip_btn.clicked.connect(self.flip_board);foot.addWidget(self.flip_btn);setup_col.addLayout(foot);play_col.addWidget(setup)
        self.mode_stack.addWidget(play_page)

        review_page=QWidget();review_col=QVBoxLayout(review_page);review_col.setContentsMargins(0,0,0,0);review_col.setSpacing(12)
        review_title=QLabel("GAME REVIEW");review_title.setObjectName("SectionTitle");review_col.addWidget(review_title)
        self.review_summary=QLabel("Run a review to compare both sides and find the game’s turning points.");self.review_summary.setObjectName("ReviewSummary");self.review_summary.setWordWrap(True);self.review_summary.setToolTip(MOVE_QUALITY_TOOLTIP);review_col.addWidget(self.review_summary)
        metrics=QHBoxLayout();self.review_metric_white=self._metric_card("WHITE MOVE QUALITY","—");self.review_metric_black=self._metric_card("BLACK MOVE QUALITY","—");self.review_metric_turning=self._metric_card("TURNING POINT","—");self.review_metric_white.setToolTip(MOVE_QUALITY_TOOLTIP);self.review_metric_black.setToolTip(MOVE_QUALITY_TOOLTIP);self.review_metric_turning.setToolTip(REVIEW_IMPACT_TOOLTIP);metrics.addWidget(self.review_metric_white);metrics.addWidget(self.review_metric_black);metrics.addWidget(self.review_metric_turning);review_col.addLayout(metrics)
        self.momentum_chart=MomentumChart(player_color=self.player_color);self.momentum_chart.setMinimumHeight(138);self.momentum_chart.setMaximumHeight(165);self.momentum_chart.move_selected.connect(self.jump_to_ply);review_col.addWidget(self.momentum_chart)
        review_tools=QHBoxLayout();self.review_filter=QComboBox();self.review_filter.addItems(["All moves","Errors","Missed chances","Good moves","Brilliant / Great","Tactical ideas","Book moves","Opening","Middlegame","Endgame"]);self.review_filter.setToolTip("Filter to a type of move or jump to a game phase. Combine with the side filter.");self.review_filter.currentIndexChanged.connect(self._render_history);review_tools.addWidget(self.review_filter,2)
        self.review_side_filter=QComboBox();self.review_side_filter.addItems(["Both sides","My moves","White","Black"]);self.review_side_filter.currentIndexChanged.connect(self._render_history);self.review_side_filter.setToolTip("Choose whose moves to show. Combine this with the move-type filter.");review_tools.addWidget(self.review_side_filter,1)
        self.analysis_level=QComboBox();self.analysis_level.addItem("Quick",.12);self.analysis_level.addItem("Standard",.4);self.analysis_level.addItem("Thorough",.8);self.analysis_level.setCurrentIndex(1);self.analysis_level.setToolTip("Standard and Thorough allow deeper move comparisons and can confirm unique resources; longer games take more time.");review_tools.addWidget(self.analysis_level);review_col.addLayout(review_tools)
        self.review_table=QTableWidget(0,5);self.review_table.setHorizontalHeaderLabels(["#","Move","Review","Impact","Eval"]);self.review_table.setToolTip(REVIEW_IMPACT_TOOLTIP);self._configure_table(self.review_table)
        hh=self.review_table.horizontalHeader();hh.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);hh.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);hh.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);hh.setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents);hh.setSectionResizeMode(4,QHeaderView.ResizeMode.ResizeToContents)
        self.review_table.cellClicked.connect(self.jump_to_row)
        self.review_table.currentCellChanged.connect(
            lambda row,column,_previous_row,_previous_column:self.jump_to_row(row,column) if row>=0 else None)
        review_col.addWidget(self.review_table,1)
        detail_card=QFrame();detail_card.setObjectName("ReviewCard");detail_col=QVBoxLayout(detail_card);detail_col.setContentsMargins(16,14,16,14);detail_col.setSpacing(7)
        detail_head=QHBoxLayout();self.detail_verdict=QLabel("READY");self.detail_verdict.setObjectName("MoveBadge");detail_head.addWidget(self.detail_verdict);detail_head.addStretch();self.detail_move=QLabel("Choose a reviewed move");self.detail_move.setObjectName("PlayerName");detail_head.addWidget(self.detail_move)
        self.speak_review_btn=self._button("Read aloud","secondary");self.speak_review_btn.setCheckable(True);self.speak_review_btn.setToolTip("Read this coaching feedback aloud and continue as you move through the review.")
        self.speak_review_btn.setEnabled(QTextToSpeech is not None)
        if QTextToSpeech is None:self.speak_review_btn.setToolTip("Install the PyQt6 TextToSpeech add-on to enable spoken review feedback.")
        self.speak_review_btn.toggled.connect(self.toggle_review_speech);detail_head.addWidget(self.speak_review_btn);detail_col.addLayout(detail_head)
        self.detail_metrics=QLabel("Move quality, evaluation and engine alternatives will appear here.");self.detail_metrics.setObjectName("Muted");self.detail_metrics.setWordWrap(True);self.detail_metrics.setToolTip(MOVE_QUALITY_TOOLTIP+"\n\n"+REVIEW_IMPACT_TOOLTIP);detail_col.addWidget(self.detail_metrics)
        self.detail=QLabel("Play a game or import a PGN, then choose Review Game for a move-by-move breakdown.");self.detail.setObjectName("ReviewLesson");self.detail.setWordWrap(True);self.detail.setAlignment(Qt.AlignmentFlag.AlignTop);detail_col.addWidget(self.detail)
        self.detail_prompt=QLabel("");self.detail_prompt.setObjectName("CoachPrompt");self.detail_prompt.setWordWrap(True);self.detail_prompt.hide();detail_col.addWidget(self.detail_prompt)
        self.retry_move_btn=self._button("Try this position","secondary");self.retry_move_btn.clicked.connect(self.practice_current_mistake);self.retry_move_btn.setVisible(False);detail_col.addWidget(self.retry_move_btn)
        self.detail_line=QLabel("");self.detail_line.setObjectName("Muted");self.detail_line.setWordWrap(True);detail_col.addWidget(self.detail_line)
        self.detail_scroll=QScrollArea();self.detail_scroll.setWidgetResizable(True);self.detail_scroll.setMinimumHeight(175);self.detail_scroll.setWidget(detail_card);review_col.addWidget(self.detail_scroll)
        nav=QHBoxLayout();self.prev_btn=self._button("‹","secondary");self.prev_btn.setToolTip("Previous move matching the selected filters (Left Arrow)");self.prev_btn.clicked.connect(lambda:self.navigate(-1));nav.addWidget(self.prev_btn)
        self.next_btn=self._button("›","secondary");self.next_btn.setToolTip("Next move matching the selected filters (Right Arrow)");self.next_btn.clicked.connect(lambda:self.navigate(1));nav.addWidget(self.next_btn)
        self.reply_view_btn=self._button("Show reply","secondary");self.reply_view_btn.setCheckable(True);self.reply_view_btn.setEnabled(False);self.reply_view_btn.toggled.connect(self.toggle_review_reply);nav.addWidget(self.reply_view_btn)
        self.best_view_btn=self._button("Show best","secondary");self.best_view_btn.setCheckable(True);self.best_view_btn.toggled.connect(self.toggle_review_best);nav.addWidget(self.best_view_btn)
        self.review_btn=self._button("Review Game","primary");self.review_btn.clicked.connect(self.start_review);nav.addWidget(self.review_btn,1);review_col.addLayout(nav)
        pv_row=QHBoxLayout();self.pv_step_btn=self._button("Step through best line","secondary");self.pv_step_btn.setToolTip("Replay the engine's preferred continuation before the move was played.");self.pv_step_btn.clicked.connect(self.step_review_pv);self.pv_step_btn.setEnabled(False);pv_row.addWidget(self.pv_step_btn);self.reply_pv_step_btn=self._button("Step through reply line","secondary");self.reply_pv_step_btn.setToolTip("Replay Stockfish's strongest response line after the move that was played.");self.reply_pv_step_btn.clicked.connect(self.step_review_reply_pv);self.reply_pv_step_btn.setEnabled(False);pv_row.addWidget(self.reply_pv_step_btn);pv_row.addStretch();self.export_review_btn=self._button("Export annotated PGN","secondary");self.export_review_btn.setEnabled(False);self.export_review_btn.clicked.connect(self.export_review_pgn);pv_row.addWidget(self.export_review_btn);review_col.addLayout(pv_row)
        key_nav=QHBoxLayout();self.prev_key_moment_btn=self._button("Previous key moment","secondary");self.prev_key_moment_btn.clicked.connect(self.previous_key_moment);key_nav.addWidget(self.prev_key_moment_btn)
        self.key_moment_btn=self._button("Next key moment","primary");self.key_moment_btn.clicked.connect(self.next_key_moment);key_nav.addWidget(self.key_moment_btn,1);review_col.addLayout(key_nav)
        self.progress=QProgressBar();self.progress.setTextVisible(False);self.progress.setFixedHeight(5);self.progress.setValue(0);review_col.addWidget(self.progress);self.mode_stack.addWidget(review_page)

        coach_page=QWidget();coach_col=QVBoxLayout(coach_page);coach_col.setContentsMargins(0,0,0,0);coach_col.setSpacing(14)
        coach_title=QLabel("YOUR COACH");coach_title.setObjectName("SectionTitle");coach_col.addWidget(coach_title)
        self.coach_overview=QLabel("Finish a game and run review to get a focused lesson plan.");self.coach_overview.setObjectName("CoachHero");self.coach_overview.setWordWrap(True);coach_col.addWidget(self.coach_overview)
        self.coach_goal=QLabel("Next focus: complete a game for personalised feedback.");self.coach_goal.setObjectName("ReviewSummary");self.coach_goal.setWordWrap(True);coach_col.addWidget(self.coach_goal)
        self.coach_opening=QLabel("Opening: no local line detected yet.");self.coach_opening.setObjectName("ReviewSummary");self.coach_opening.setWordWrap(True);coach_col.addWidget(self.coach_opening)
        coach_col.addStretch()
        self.coach_review_btn=self._button("Review this game","primary");self.coach_review_btn.clicked.connect(self.start_review);coach_col.addWidget(self.coach_review_btn)
        self.practice_btn=self._button("Retry mistakes & missed chances","secondary");self.practice_btn.clicked.connect(self.practice_mistakes);coach_col.addWidget(self.practice_btn)
        self.explore_btn=self._button("Explore this opening","secondary");self.explore_btn.clicked.connect(self.explore_openings);coach_col.addWidget(self.explore_btn)
        self.coach_puzzles_btn=self._button("Open puzzle studio","secondary");self.coach_puzzles_btn.clicked.connect(self.open_puzzles);coach_col.addWidget(self.coach_puzzles_btn);self.mode_stack.addWidget(coach_page)
        splitter.addWidget(panel); splitter.setSizes([1660,820])
        self.statusBar().showMessage("Offline play · your moves stay on this computer")
        if self.profile_error:self.statusBar().showMessage(f"Profile storage unavailable: {self.profile_error}")
        self.setStyleSheet(self._stylesheet())

    def _button(self,text,kind):
        names={"secondary":"Button","primary":"PrimaryButton","danger":"DangerButton","mode":"ModeButton"}
        btn=QPushButton(text); btn.setObjectName(names.get(kind,"Button")); btn.setCursor(Qt.CursorShape.PointingHandCursor); return btn
    def _configure_table(self,table):
        table.verticalHeader().hide();table.verticalHeader().setDefaultSectionSize(38);table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.setAlternatingRowColors(True);table.setShowGrid(False)
    def _metric_card(self,label,value):
        card=QFrame();card.setObjectName("MetricCard");layout=QVBoxLayout(card);layout.setContentsMargins(11,8,11,8);layout.setSpacing(2)
        caption=QLabel(label);caption.setObjectName("MetricCaption");layout.addWidget(caption)
        metric=QLabel(value);metric.setObjectName("MetricValue");layout.addWidget(metric)
        card.metric=metric
        return card
    def set_mode(self,mode):
        indexes={"play":0,"review":1,"coach":2}
        if mode not in indexes:return
        if mode!="review" and self.review_speech:
            self.review_speech.stop()
            if self.speak_review_btn.isChecked():self.speak_review_btn.setChecked(False)
        self.mode_stack.setCurrentIndex(indexes[mode])
        for key,button in self.mode_buttons.items():
            button.setProperty("active",key==mode);button.style().unpolish(button);button.style().polish(button)
    def toggle_fullscreen(self):
        if self.isFullScreen():self.showNormal()
        else:self.showFullScreen()
        self.fullscreen_btn.setText("Exit fullscreen" if self.isFullScreen() else "Enter fullscreen")

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_F11:
            self.toggle_fullscreen();event.accept();return
        if event.key()==Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen();event.accept();return
        if self.reviewing and self.history and event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right):
            self.navigate(-1 if event.key()==Qt.Key.Key_Left else 1);event.accept();return
        super().keyPressEvent(event)

    def _small(self,text):
        label=QLabel(text); label.setObjectName("Muted"); return label
    def _stylesheet(self):
        return """
        QWidget { background:#1b1d1f; color:#e6e9eb; font-family:'Segoe UI',Arial,sans-serif; font-size:15px; }
        QMainWindow { background:#1b1d1f; }
        #Brand { color:#f1f3f4; font-size:30px; font-weight:800; letter-spacing:1px; }
        #Muted { color:#aab2b5; font-size:14px; }
        #OpeningPill { background:#292d30; color:#a9d7b4; border-radius:12px; padding:8px 12px; font-weight:600; }
        #PlayerCard,#SidePanel { background:#242729; border:1px solid #303639; border-radius:12px; }
        #InsetCard,#MetricCard,#ReviewCard { background:#1e2224; border:1px solid #343a3d; border-radius:10px; }
        #MetricCaption { color:#a8b1b4; font-size:10px; font-weight:800; letter-spacing:1px; }
        #MetricValue { color:#f3f5f4; font-size:17px; font-weight:800; }
        #MoveBadge { background:#3d6949; color:#f4fff5; border-radius:8px; padding:6px 9px; font-size:11px; font-weight:800; letter-spacing:.6px; }
        #ReviewLesson { color:#edf0ee; background:#2a302c; border-left:3px solid #81b64c; border-radius:5px; padding:10px 12px; }
        #CoachPrompt { color:#e8e2c9; background:#302f27; border-left:3px solid #c3a95c; border-radius:5px; padding:9px 12px; }
        #CoachHero { background:#26352a; color:#eef6e9; border:1px solid #42694c; border-radius:12px; padding:17px; font-size:16px; font-weight:700; }
        #Avatar { color:#e5e8e9; background:#363b3e; border-radius:21px; font-size:22px; min-width:42px; min-height:42px; qproperty-alignment: AlignCenter; }
        #PlayerName { font-size:16px; font-weight:700; color:#eef0f1; }
        #ChessComClock { color:#f3f5f4; background:#181b1d; border:1px solid #42494c; border-radius:8px; padding:8px 12px; font-size:20px; font-weight:800; font-family:Consolas,monospace; }
        #SectionTitle { color:#a3abb0; font-size:13px; font-weight:800; letter-spacing:1.4px; }
        #ReviewDetail { background:#1e2224; color:#d9dedf; border:1px solid #32383b; border-radius:10px; padding:14px; }
        #AccentText { color:#9ed3ac; font-weight:800; font-size:19px; }
        #ReviewSummary { background:#1e2224; color:#d4dcdd; border:1px solid #343a3d; border-radius:10px; padding:12px; font-size:13px; }
        QDialog { background:#1b1d1f; }
        QComboBox,QSpinBox { background:#292e31; color:#e6e9eb; border:1px solid #42494c; border-radius:8px; padding:9px 11px; min-height:24px; }
        QComboBox:hover,QSpinBox:hover { border-color:#668f68; }
        QComboBox::drop-down { border:0; width:26px; }
        QComboBox QAbstractItemView { background:#292e31; color:#e6e9eb; border:1px solid #42494c; selection-background-color:#385b41; padding:5px; }
        QPushButton { background:#343a3d; color:#e6e9eb; border:1px solid #42494c; border-radius:8px; padding:12px 15px; font-weight:600; min-height:30px; }
        QPushButton:hover { background:#41494c; }
        QPushButton#Button { background:#343a3d; border:1px solid #42494c; border-radius:8px; padding:12px 15px; font-weight:600; }
        QPushButton#Button:hover { background:#41494c; }
        QPushButton#PrimaryButton { background:#81b64c; color:#162116; border:0; border-radius:8px; padding:13px 16px; font-weight:800; }
        QPushButton#PrimaryButton:hover { background:#91c55b; }
        QPushButton#DangerButton { background:#4a2c2d; color:#f0b3ae; border:1px solid #684044; border-radius:8px; padding:12px 15px; font-weight:700; }
        QPushButton#DangerButton:hover { background:#5a3436; }
        QPushButton#ModeButton { background:#202426; color:#aeb7ba; border:1px solid #343a3d; border-radius:8px; padding:10px 8px; min-height:22px; font-weight:800; }
        QPushButton#ModeButton:hover { background:#2c3334; color:#eff2f1; }
        QPushButton#ModeButton[active="true"] { background:#385b41; color:#eff9ec; border-color:#75a15f; }
        QPushButton:focus,QComboBox:focus,QSlider:focus,QCheckBox:focus,QTableWidget:focus { border:2px solid #9bcf68; }
        QPushButton:disabled { color:#727b7e; background:#292d2f; border-color:#34393b; }
        QTableWidget { background:#202426; alternate-background-color:#252a2c; border:1px solid #343a3d; border-radius:8px; selection-background-color:#385b41; selection-color:#ffffff; outline:0; }
        QHeaderView::section { background:#292e31; color:#9fa8ac; border:0; padding:11px 8px; font-size:12px; font-weight:800; }
        QTableWidget::item { padding:10px 8px; border:0; }
        QTableWidget::item:selected { background:#385b41; color:#ffffff; }
        QSlider::groove:horizontal { height:5px; background:#3b4144; border-radius:2px; }
        QSlider::handle:horizontal { background:#91c65b; width:15px; margin:-5px 0; border-radius:7px; }
        QCheckBox { color:#aab1b4; spacing:7px; }
        QCheckBox::indicator { width:19px; height:19px; }
        QScrollBar:vertical { background:#202426; width:14px; margin:3px; border-radius:7px; }
        QScrollBar::handle:vertical { background:#4a5357; min-height:30px; border-radius:6px; }
        QScrollBar::handle:vertical:hover { background:#657176; }
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
        QProgressBar { background:#33393c; border:0; border-radius:2px; }
        QProgressBar::chunk { background:#83bc69; border-radius:2px; }
        QStatusBar { color:#8d969a; border-top:1px solid #2d3235; }
        QToolTip { color:#fff; background:#333; border:1px solid #555; }
        """

    def _load_default_openings(self):
        candidates=[]
        for folder in (BUNDLE_DIR,BUNDLE_DIR/"openings",APP_DIR,APP_DIR/"openings"):
            candidates.extend(discover_lichess_files(folder))
        if candidates: self._load_paths_async(candidates)

    def load_opening_dialog(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose the Lichess chess-openings folder",str(APP_DIR))
        if not folder: return
        paths=discover_lichess_files(Path(folder))
        if not paths:
            QMessageBox.information(self,"Lichess opening files not found","Choose the folder containing a.tsv, b.tsv, c.tsv, d.tsv, and e.tsv.")
            return
        self._load_paths_async(paths)

    def _load_paths_async(self, paths):
        self.opening_label.setText("Loading Lichess openings…")
        def load():
            entries=load_openings(paths)
            self.openings_loaded.emit(entries,paths)
        threading.Thread(target=load,daemon=True,name="Opening database loader").start()

    def _set_openings(self,entries,paths):
        self.openings=entries
        self.opening_label.setText(f"{len(entries):,} Lichess openings" if entries else "Opening database not loaded")
        self.opening_label.setToolTip("\n".join(str(p) for p in paths))
        self.statusBar().showMessage(f"Loaded {len(entries):,} opening lines from Lichess TSV",5000)
        if paths and not entries:
            QMessageBox.warning(self,"No openings loaded","The selected TSV files were found, but no valid opening lines could be read.")
        self._update_opening()
        if hasattr(self,"chesscom_opening_mastery"):
            self.refresh_chesscom_library(self.chesscom_username.text())

    def load_book_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,"Load a weighted Polyglot opening book",str(APP_DIR),"Polyglot books (*.bin)")
        if not path:return
        try:
            with chess.polyglot.open_reader(path):
                pass
            self.book_path=path
            self.book_btn.setText(f"Book: {Path(path).name}")
            self.book_btn.setToolTip("Weighted offline book loaded. Book moves and response frequencies will appear in reviews and Explore.")
            self.statusBar().showMessage(f"Loaded weighted book · {Path(path).name}",6000)
        except Exception as exc:
            QMessageBox.warning(self,"Could not load opening book",f"Choose a valid Polyglot .bin opening book.\n\n{exc}")

    def _engine_started(self,info):
        self.engine_startup_timer.stop();self.engine_startup_timed_out=False
        self.engine_ready=True; self.engine_status.setText("Stockfish 19 · ready"); self.engine_status.setStyleSheet("color:#91ca9c")
        self.syzygy_supported="SyzygyPath" in info.get("options",set())
        self.syzygy_active=bool(info.get("syzygy_active",False))
        self.syzygy_error=info.get("syzygy_error","")
        self._update_syzygy_button()
        worker_path=normalized_syzygy_path(getattr(self.worker,"syzygy_path",""))
        if (self.syzygy_supported and not self.syzygy_error
                and (worker_path!=normalized_syzygy_path(self.syzygy_path)
                     or bool(getattr(self.worker,"syzygy_active",False))!=bool(self.syzygy_path))):
            self.syzygy_request.emit(self.syzygy_path)
        self.elo_request.emit(self.elo_slider.value())
        if self.board.turn!=self.player_color and not self.history:self._after_move()
    def _update_syzygy_button(self):
        if self.syzygy_error:
            self.syzygy_btn.setText("Tablebases · Fix path")
            self.syzygy_btn.setToolTip(self.syzygy_error)
        elif self.syzygy_active:
            self.syzygy_btn.setText("Tablebases · On")
            self.syzygy_btn.setToolTip(f"Syzygy probing is active from {self.syzygy_path}. Click to change or disable it.")
        elif self.syzygy_path:
            self.syzygy_btn.setText("Tablebases · Pending")
            self.syzygy_btn.setToolTip(f"Selected folder: {self.syzygy_path}. It will be configured when Stockfish is ready.")
        elif self.engine_ready and not self.syzygy_supported:
            self.syzygy_btn.setText("Tablebases · Unsupported")
            self.syzygy_btn.setToolTip("This Stockfish build does not expose SyzygyPath.")
        else:
            self.syzygy_btn.setText("Tablebases · Off")
            self.syzygy_btn.setToolTip("Use optional local Syzygy endgame tablebases for exact results with up to seven pieces.")
        self.syzygy_btn.setEnabled(not self.review_busy and (not self.engine_ready or self.syzygy_supported))
    def open_syzygy_menu(self):
        menu=QMenu(self)
        choose=menu.addAction("Choose Syzygy tablebase folder…")
        disable=menu.addAction("Disable tablebases")
        disable.setEnabled(bool(self.syzygy_path))
        chosen=menu.exec(self.syzygy_btn.mapToGlobal(self.syzygy_btn.rect().bottomLeft()))
        if chosen==choose:
            folder=QFileDialog.getExistingDirectory(self,"Choose the Syzygy tablebase folder",self.syzygy_path or str(APP_DIR))
            if not folder:return
            tablebase_dir=Path(folder)
            if not any(tablebase_dir.glob("*.rtbw")) and not any(tablebase_dir.glob("*.rtbz")):
                QMessageBox.information(self,"Syzygy files not found","Choose a folder containing .rtbw or .rtbz tablebase files.")
                return
            self._set_syzygy_path(str(tablebase_dir))
        elif chosen==disable:
            self._set_syzygy_path("")
    def _set_syzygy_path(self,path):
        self.syzygy_path=normalized_syzygy_path(path);self.syzygy_error="";self.syzygy_active=False
        if self.profile_store:
            try:self.profile_store.set_setting("syzygy_path",self.syzygy_path)
            except Exception as exc:self.statusBar().showMessage(f"Could not save tablebase setting: {exc}",8000)
        self._saved_game_stats_cache=None
        if self.engine_ready:
            self.syzygy_btn.setText("Tablebases · Configuring…");self.syzygy_btn.setEnabled(False)
            self.syzygy_request.emit(self.syzygy_path)
        else:self._update_syzygy_button()
    def _syzygy_configured(self,path,message,active):
        self.syzygy_active=bool(active);self.syzygy_error=message or ""
        if not message:self.syzygy_path=path
        self._saved_game_stats_cache=None
        self._update_syzygy_button()
        self.statusBar().showMessage(message or ("Syzygy tablebases enabled" if active else "Syzygy tablebases disabled"),7000)
    def _engine_startup_timeout(self):
        if self.engine_ready:return
        self.engine_startup_timed_out=True
        self.engine_status.setText("Stockfish is taking longer than expected")
        self.engine_status.setStyleSheet("color:#e2b276")
        self.statusBar().showMessage("Stockfish has not responded yet. Check STOCKFISH_PATH; the app will recover if it connects later.",12000)
    def _engine_failed(self,message):
        self.engine_startup_timer.stop()
        self.engine_status.setText("Stockfish unavailable"); self.engine_status.setStyleSheet("color:#e28b76")
        self.syzygy_supported=False;self._update_syzygy_button()
        QMessageBox.warning(self,"Stockfish unavailable",message+"\n\nSet STOCKFISH_PATH or place the engine beside this app.")
    def _engine_error(self,message):
        self.bot_busy=False; self.review_busy=False; self.review_btn.setText("Review Game");self.coach_review_btn.setText("Review this game")
        self.analysis_level.setEnabled(True)
        self._update_syzygy_button()
        if self.review_progress_dialog:self.review_progress_dialog.finish();self.review_progress_dialog=None
        self.statusBar().showMessage(message,8000)

    def change_elo(self,value):
        value=max(MIN_TARGET_ELO,min(MAX_ELO,int(value)//10*10))
        label=f"~{value}" if value<MIN_ELO else str(value)
        self.elo_value.setText(label)
        target_note=" Elo · approximate" if value<MIN_ELO else " Elo"
        self.opponent_rating.setText(f"Opponent · {'Black' if self.player_color==chess.WHITE else 'White'} · {label}{target_note}")
        if self.engine_ready: self.elo_request.emit(value)

    def choose_side(self,index):
        new_color=chess.WHITE if index==0 else chess.BLACK
        if new_color==self.player_color:return
        if self.history:
            answer=QMessageBox.question(self,"Start a new game?","Changing sides will clear the current game. Start a new game?",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Yes)
            if answer!=QMessageBox.StandardButton.Yes:
                self.side_choice.blockSignals(True); self.side_choice.setCurrentIndex(0 if self.player_color==chess.WHITE else 1); self.side_choice.blockSignals(False)
                return
        self.player_color=new_color
        self.momentum_chart.player_color=new_color
        self._update_player_labels()
        self.reset()

    def _update_player_labels(self):
        yours="White" if self.player_color==chess.WHITE else "Black"
        theirs="Black" if self.player_color==chess.WHITE else "White"
        if self.external_review_game_id and self.external_review_players:
            self.you_name.setText(f"{self.external_review_players.get(yours,yours)} · {yours}")
            self.opponent_name.setText(f"{self.external_review_players.get(theirs,theirs)} · {theirs}")
            you_rating=self.external_review_ratings.get(yours);opponent_rating=self.external_review_ratings.get(theirs)
            self.turn_label.setText(f"Chess.com · {you_rating} Elo" if you_rating else "Chess.com game")
            self.opponent_rating.setText(f"Chess.com · {opponent_rating} Elo" if opponent_rating else "Chess.com game")
        else:
            self.you_name.setText(f"You · {yours}")
            self.opponent_name.setText(f"Stockfish 19 · {theirs}")
            value=self.elo_slider.value(); target=f"~{value} Elo · approximate" if value<MIN_ELO else f"{value} Elo"
            self.opponent_rating.setText(f"Opponent · {theirs} · {target}")
            self.turn_label.setText("Your turn")
        self.you_clock.setVisible(bool(self.external_review_game_id));self.opponent_clock.setVisible(bool(self.external_review_game_id))
        self.board_widget.flipped=self.player_color==chess.BLACK
        self.board_widget.update()

    def _update_chesscom_clocks(self):
        if not self.external_review_game_id or not self.external_review_clocks:
            if hasattr(self,"you_clock"):self.you_clock.setText("—:——");self.opponent_clock.setText("—:——")
            return
        if self.reviewing and self.review_pv_active:
            position=None
        else:
            position=max(0,self.review_index-1 if self.reviewing and self.review_show_best else
                         self.review_index if self.reviewing else len(self.history))
        if position is None or position>=len(self.external_review_clocks):
            white_clock=black_clock=None
        else:white_clock,black_clock=self.external_review_clocks[position]
        user_clock=white_clock if self.player_color==chess.WHITE else black_clock
        opponent_clock=black_clock if self.player_color==chess.WHITE else white_clock
        self.you_clock.setText(format_chesscom_clock(user_clock))
        self.opponent_clock.setText(format_chesscom_clock(opponent_clock))

    def on_square(self,square):
        if self.reviewing or self.bot_busy or self._game_finished() or self.board.turn!=self.player_color: return
        if self.selected is None:
            p=self.board.piece_at(square)
            if p and p.color==self.player_color: self.selected=square
        else:
            move=chess.Move(self.selected,square); piece=self.board.piece_at(self.selected)
            if piece and piece.piece_type==chess.PAWN and chess.square_rank(square) in (0,7): move.promotion=chess.QUEEN
            if move in self.board.legal_moves:
                self._push(move); self._after_move()
            else:
                p=self.board.piece_at(square); self.selected=square if p and p.color==self.player_color else None
        self._sync_board()

    def _game_finished(self):
        return bool(self.game_result_override) or self.board.is_game_over()

    def resign(self):
        if self._game_finished() or self.reviewing:return
        answer=QMessageBox.question(self,"Resign game?","Resign this game? The result will be recorded as a loss.",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.game_result_override="0-1" if self.player_color==chess.WHITE else "1-0"
        self.turn_label.setText(f"You resigned · {self.game_result_override}")
        self.statusBar().showMessage(f"Game over · resignation · {self.game_result_override}")
        self._play_sound("game_end")

    def _push(self,move):
        if self._game_finished() or move not in self.board.legal_moves:return
        capture=self.board.is_capture(move); castle=self.board.is_castling(move)
        self.board.push(move); self.history.append(move); self.last_move=move; self.best_move=None; self.selected=None
        self.reviewing=False; self.review_index=len(self.history); self.review_moves=[];self._reset_review_surfaces("New move played · run a fresh review when the game is ready.")
        self._render_history(); self._play_sound("game_end" if self.board.is_game_over() else "check" if self.board.is_check() else "castle" if castle else "capture" if capture else "move")
        self._sync_board(); self._update_opening()
        if self.engine_ready: self.evaluation_request.emit(self.board.copy())

    def _after_move(self):
        if self._game_finished():
            if self.game_result_override:
                self.turn_label.setText(f"Game over · {self.game_result_override} · resignation")
                self.statusBar().showMessage(f"Game over · {self.game_result_override} · resignation")
                return
            outcome=self.board.outcome(); reason=outcome.termination.name.replace("_"," ").title() if outcome else "Finished"
            self.turn_label.setText(f"Game over · {self.board.result()} · {reason}"); self.statusBar().showMessage(f"Game over · {self.board.result()}"); return
        self.turn_label.setText(("Your turn · " if self.board.turn==self.player_color else "Stockfish to move · ")+ ("White" if self.board.turn else "Black"))
        if self.board.turn!=self.player_color and self.engine_ready:
            self.bot_busy=True; self.statusBar().showMessage("Stockfish is thinking…"); self.bot_request.emit((self.game_version,self.board.copy()))
    def _bot_move(self,result):
        self.bot_busy=False
        version,position,move=result
        if version!=self.game_version or position!=self.board.fen():return
        if move and move in self.board.legal_moves and not self._game_finished(): self._push(move); self._after_move()
    def request_best(self):
        if not self.engine_ready or self._game_finished() or self.reviewing:return
        self.statusBar().showMessage("Finding the best move…"); self.best_request.emit(self.board.copy())
    def _best_move(self,move):
        self.best_move=move; self._sync_board(); self.statusBar().showMessage("Best move marked on the board",4000)

    def _display_board(self):
        if not self.reviewing:return self.board
        if self.review_pv_active and 0<self.review_index<=len(self.review_moves):
            b=self.game_start_board.copy(stack=False)
            for move in self.history[:self.review_index-1]:b.push(move)
            item=self.review_moves[self.review_index-1]
            line=item.best_pv_moves if self.review_pv_kind=="best" else item.opponent_pv_moves
            if self.review_pv_kind=="reply" and self.review_index-1<len(self.history):
                played=self.history[self.review_index-1]
                if played in b.legal_moves:b.push(played)
            for move in line[:self.review_pv_index]:
                if move not in b.legal_moves:break
                b.push(move)
            return b
        b=self.game_start_board.copy(stack=False)
        shown_plies=max(0,self.review_index-1) if self.review_show_best else self.review_index
        for move in self.history[:shown_plies]: b.push(move)
        return b
    def _sync_board(self):
        shown=self._display_board()
        last=None if self.reviewing else self.last_move
        if self.reviewing and self.review_pv_active and self.review_pv_index:
            item=self.review_moves[self.review_index-1]
            line=item.best_pv_moves if self.review_pv_kind=="best" else item.opponent_pv_moves
            pv_index=min(self.review_pv_index,len(line))
            last=line[pv_index-1] if pv_index else None
        elif self.reviewing and self.review_index:
            last_index=self.review_index-2 if self.review_show_best else self.review_index-1
            last=self.history[last_index] if 0<=last_index<len(self.history) else None
        best=self.best_move if not self.reviewing else None
        if self.reviewing and 0<self.review_index<=len(self.review_moves) and not self.review_pv_active:
            reviewed=self.review_moves[self.review_index-1]
            if self.review_show_best:best=reviewed.best_move
            elif self.review_show_reply:best=reviewed.opponent_move
        sticker=None;sticker_color="#4baf73"
        if self.reviewing and 0<self.review_index<=len(self.review_moves):
            reviewed=self.review_moves[self.review_index-1];sticker=reviewed.verdict
            sticker_color=reviewed.color
            if reviewed.book_status.startswith("Book move"):sticker+=" · BOOK"
            if self.review_pv_active:
                line=reviewed.best_pv_moves if self.review_pv_kind=="best" else reviewed.opponent_pv_moves
                sticker=f"{'Best' if self.review_pv_kind=='best' else 'Reply'} line {min(self.review_pv_index,len(line))}/{len(line)}";sticker_color="#39a982"
        focus=()
        if self.reviewing and not self.review_pv_active and 0<self.review_index<=len(self.review_moves):
            focus=review_focus_squares(self.review_moves[self.review_index-1].events)
        self.board_widget.set_position(shown,last,self.selected,best,sticker,sticker_color,focus)
        self._update_chesscom_clocks()
        self.momentum_chart.set_values([item.eval_after for item in self.review_moves],
                                       self.review_index if self.reviewing else 0,self.review_moves)
        if self.reviewing:
            self.play_hint.setText("Viewing a past position. Switch to Review for the guided explanation and best line.")
        elif self._game_finished():
            self.play_hint.setText("Game complete. Open Review to see the decisive moments and your next lesson.")
        else:
            turn="Your move" if self.board.turn==self.player_color else "Stockfish is thinking"
            self.play_hint.setText(f"{turn} · {len(self.history)} half-moves played · {detect_opening(self.history,self.openings)}")
    def _clear_review_overlays(self):
        self.review_show_best=False;self.review_show_reply=False
        self.best_view_btn.setChecked(False);self.reply_view_btn.setChecked(False)
        self.best_view_btn.setText("Show best");self.reply_view_btn.setText("Show reply")

    def toggle_review_best(self,show):
        self.review_show_best=bool(show)
        if show:
            self.review_show_reply=False;self.reply_view_btn.setChecked(False);self.review_pv_active=False;self.review_pv_index=0
        self.best_view_btn.setText("Show played" if show else "Show best")
        self._sync_board()

    def toggle_review_reply(self,show):
        self.review_show_reply=bool(show)
        if show:
            self.review_show_best=False;self.best_view_btn.setChecked(False);self.review_pv_active=False;self.review_pv_index=0
        self.reply_view_btn.setText("Hide reply" if show else "Show reply")
        self._sync_board()

    def step_review_pv(self):
        self._step_review_line("best")
    def step_review_reply_pv(self):
        self._step_review_line("reply")
    def _step_review_line(self,kind):
        if not self.reviewing or not 0<self.review_index<=len(self.review_moves):return
        item=self.review_moves[self.review_index-1]
        line=item.best_pv_moves if kind=="best" else item.opponent_pv_moves
        if not line:return
        if not self.review_pv_active or self.review_pv_kind!=kind:self.review_pv_index=0
        self.review_pv_active=True;self.review_show_best=False;self.review_show_reply=False;self.best_view_btn.setChecked(False);self.reply_view_btn.setChecked(False)
        self.review_pv_kind=kind
        if self.review_pv_index>=len(line):self.review_pv_index=0
        self.review_pv_index+=1
        if kind=="best":
            self.pv_step_btn.setText("Restart best line" if self.review_pv_index>=len(line) else "Next best-line move")
            self.reply_pv_step_btn.setText("Step through reply line")
        else:
            self.pv_step_btn.setText("Step through best line")
            self.reply_pv_step_btn.setText("Restart reply line" if self.review_pv_index>=len(line) else "Next reply-line move")
        self._sync_board()
    def _render_history(self):
        filter_name=self.review_filter.currentText()
        side_filter=self.review_side_filter.currentText()
        visible=review_filter_indices(self.review_moves,len(self.history),filter_name,self.player_color,self.game_start_board.turn,side_filter)
        self.visible_review_plies=visible
        self.review_table.setRowCount(len(visible))
        b=self.game_start_board.copy(stack=False); sans=[];labels=[];white_to_move=[]
        for move in self.history:
            sans.append(b.san(move));labels.append(f"{b.fullmove_number}{'.' if b.turn==chess.WHITE else '…'}");white_to_move.append(b.turn==chess.WHITE);b.push(move)
        self.move_table.setRowCount(len(self.history))
        for row,san in enumerate(sans):
            item=self.review_moves[row] if row<len(self.review_moves) else None
            values=[labels[row],san,evaluation_label(item.eval_after,item.mate_after) if item else "—"]
            for column,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if column==1 and white_to_move[row]:cell.setForeground(QColor("#f0f2f3"))
                self.move_table.setItem(row,column,cell)
            self.move_table.item(row,0).setData(Qt.ItemDataRole.UserRole,row)
        for row,i in enumerate(visible):
            san=sans[i]; item=self.review_moves[i] if i<len(self.review_moves) else None
            shown_san=san+("  · book" if item and item.book_status.startswith("Book move") else "")
            values=[labels[i],shown_san,item.verdict if item else "",format_review_impact(item.expected_loss,item.loss) if item else "",eval_text(item.eval_after) if item else ""]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if col==2 and item: cell.setForeground(QColor(item.color)); cell.setFont(QFont("Segoe UI",9,QFont.Weight.Bold))
                if col==1 and white_to_move[i]: cell.setForeground(QColor("#f0f2f3"))
                self.review_table.setItem(row,col,cell)
            self.review_table.item(row,0).setData(Qt.ItemDataRole.UserRole,i)
        if self.reviewing:
            if visible:
                current=max(0,self.review_index-1)
                target_row=(visible.index(current) if current in visible else
                            min(range(len(visible)),key=lambda row:abs(visible[row]-current)))
                self.review_table.selectRow(target_row)
                self.review_table.scrollToItem(self.review_table.item(target_row,0))
                if visible[target_row]!=current:self.jump_to_row(target_row,0)
            else:
                self.review_table.clearSelection()
                self.statusBar().showMessage(
                    f"No reviewed moves match {filter_name.lower()} for {side_filter.lower()}.",5000)
        self._update_key_moment_controls()
    def jump_to_game_row(self,row,_col):
        if not self.review_moves or row<0 or row>=len(self.history):
            self.statusBar().showMessage("Run Game Review to step through past moves with engine guidance.",4500);return
        self.set_mode("review")
        if row in self.visible_review_plies:self.jump_to_row(self.visible_review_plies.index(row),0)
        else:
            self.review_filter.setCurrentIndex(0);self.review_side_filter.setCurrentIndex(0);self.jump_to_row(row,0)
    def jump_to_row(self,row,_col):
        ply=review_row_ply(self.visible_review_plies,row)
        if not self.history or ply is None:return
        if self.reviewing and self.review_index==ply+1:return
        self.reviewing=True; self.review_index=ply+1; self.selected=None; self.best_move=None;self.review_pv_active=False;self.review_pv_index=0;self._clear_review_overlays()
        self._sync_board(); self._show_detail()
    def navigate(self,delta):
        if not self.history:return
        visible=self.visible_review_plies
        if not visible:
            self.statusBar().showMessage("No moves match the current review filters.",4000);return
        current=self.review_index-1
        if current in visible:
            position=visible.index(current)+ (1 if delta>0 else -1)
            target=visible[position] if 0<=position<len(visible) else None
        elif delta>0:
            target=next((ply for ply in visible if ply>current),None)
        else:
            target=next((ply for ply in reversed(visible) if ply<current),None)
        if target is None:
            direction="next" if delta>0 else "previous"
            filter_name=self.review_filter.currentText().lower()
            self.statusBar().showMessage(f"No {direction} move in the current {filter_name} filter.",4000);return
        self.jump_to_ply(target+1)

    def jump_to_ply(self,ply):
        if not self.review_moves:return
        self.reviewing=True;self.review_index=max(1,min(len(self.history),int(ply)));self.review_pv_active=False;self.review_pv_index=0;self._clear_review_overlays();self._sync_board();self._show_detail()
        move_index=self.review_index-1
        if move_index in self.visible_review_plies:self.review_table.selectRow(self.visible_review_plies.index(move_index))
        else:self.review_table.clearSelection()

    def _key_moments(self):
        moments=review_moment_tags(self.review_moves)
        opening_ply=find_opening_ply(self.history,self.openings)
        book_plies=[item.ply for item in self.review_moves if item.book_status.startswith("Book move")]
        if book_plies:opening_ply=max(book_plies)
        if opening_ply:moments.setdefault(opening_ply,[]).append("Last documented opening move")
        return moments

    def _update_key_moment_controls(self):
        """Preview the next and previous curated moments in this game's review."""
        moments=self._key_moments() if self.review_moves else {}
        previous=[ply for ply in moments if ply<self.review_index]
        upcoming=[ply for ply in moments if ply>self.review_index]
        controls=((self.prev_key_moment_btn,previous,"Previous"),
                  (self.key_moment_btn,upcoming,"Next"))
        for button,plies,direction in controls:
            if not plies:
                button.setEnabled(False)
                button.setText(f"{direction} key moment")
                button.setToolTip("No earlier key moments in this game." if direction=="Previous"
                                  else "No later key moments in this game.")
                continue
            target=plies[-1] if direction=="Previous" else plies[0]
            labels=moments[target]
            label=labels[0] if labels else "Key moment"
            button.setEnabled(True)
            button.setText(f"{direction} · {label}")
            item=next((move for move in self.review_moves if move.ply==target),None)
            move_text=review_move_notation(item) if item else f"ply {target}"
            button.setToolTip(f"Jump to {move_text}: {'; '.join(labels)}")

    def toggle_review_speech(self,enabled):
        self.speak_review_btn.setText("Stop reading" if enabled else "Read aloud")
        if not enabled:
            if self.review_speech:self.review_speech.stop()
            self._last_spoken_ply=None
            return
        if not self.review_index or self.review_index>len(self.review_moves):
            self.speak_review_btn.setChecked(False)
            self.statusBar().showMessage("Choose a reviewed move before starting read aloud.",5000)
            return
        if QTextToSpeech is None:
            self.speak_review_btn.setChecked(False)
            self.statusBar().showMessage("Text-to-speech is unavailable. Install the PyQt6 TextToSpeech add-on.",7000)
            return
        try:
            if self.review_speech is None:
                self.review_speech=QTextToSpeech(self)
                self.review_speech.errorOccurred.connect(self._review_speech_error)
            if self.review_speech.state()==QTextToSpeech.State.Error:
                raise RuntimeError(self.review_speech.errorString() or "No speech engine is available")
            self._speak_review_item(self.review_moves[self.review_index-1])
        except Exception as exc:
            self.speak_review_btn.setChecked(False)
            self.statusBar().showMessage(f"Read aloud is unavailable: {exc}",7000)

    def _speak_review_item(self,item):
        if not self.review_speech:return
        if self._last_spoken_ply==item.ply:return
        self.review_speech.stop()
        self._last_spoken_ply=item.ply
        self.review_speech.say(review_spoken_text(item,self.player_color))

    def _review_speech_error(self,_reason,message):
        if self.speak_review_btn.isChecked():self.speak_review_btn.setChecked(False)
        self.statusBar().showMessage(f"Read aloud stopped: {message}",7000)

    def next_key_moment(self):
        if not self.review_moves:
            self.statusBar().showMessage("Run a full review first to find key moments.",5000);return
        moments=self._key_moments()
        upcoming=[ply for ply in sorted(moments) if ply>self.review_index]
        if not upcoming:
            self.statusBar().showMessage("No more key moments. Use the move arrows to continue.",5000);return
        target=upcoming[0];self.jump_to_ply(target)
        self.statusBar().showMessage("Key moment · "+" + ".join(moments[target]),6000)
    def previous_key_moment(self):
        if not self.review_moves:
            self.statusBar().showMessage("Run a full review first to find key moments.",5000);return
        moments=self._key_moments()
        previous=[ply for ply in sorted(moments) if ply<self.review_index]
        if not previous:
            self.statusBar().showMessage("No earlier key moments. Use the move arrows to continue.",5000);return
        target=previous[-1];self.jump_to_ply(target)
        self.statusBar().showMessage("Key moment · "+" + ".join(moments[target]),6000)
    def _show_detail(self):
        self._update_key_moment_controls()
        if not self.review_index or self.review_index>len(self.review_moves):
            if self.review_speech:self.review_speech.stop()
            self._last_spoken_ply=None
            if self.speak_review_btn.isChecked():self.speak_review_btn.setChecked(False)
            self.detail_verdict.setText("READY");self.detail_verdict.setStyleSheet("")
            self.detail_move.setText("Choose a reviewed move")
            self.detail_metrics.setText("Move quality, evaluation and engine alternatives will appear here.")
            self.detail.setText("Review this game to see the best move, evaluation change, explanation, and principal variation.");self.detail_prompt.clear();self.detail_prompt.hide();self.detail_line.setText("");self.pv_step_btn.setEnabled(False);self.reply_pv_step_btn.setEnabled(False);self.retry_move_btn.setVisible(False);self.reply_view_btn.setEnabled(False);return
        item=self.review_moves[self.review_index-1]
        if self.speak_review_btn.isChecked():self._speak_review_item(item)
        self.retry_move_btn.setVisible(True)
        user_side="White" if self.player_color==chess.WHITE else "Black"
        move_perspective="Your move" if item.side==user_side else "Opponent's move"
        book=item.book_status
        key_moment=" · ".join(self._key_moments().get(item.ply,[]))
        self.detail_verdict.setText(item.verdict.upper());self.detail_verdict.setStyleSheet(f"background:{item.color};color:#ffffff;border-radius:8px;padding:6px 9px;font-size:11px;font-weight:800;letter-spacing:.6px;")
        self.detail_verdict.setToolTip(review_verdict_tooltip(item))
        self.detail_move.setText(f"{review_move_notation(item)} · {move_perspective} · {item.side}")
        impact=f"{item.expected_loss*100:.1f} pp expected score lost vs best · {item.loss} cp"
        evaluation=f"{evaluation_label(item.eval_before,item.mate_before)} → {evaluation_label(item.eval_after,item.mate_after)}"
        self.detail_metrics.setText(
            f"Move quality: {item.accuracy:.0f}% · Impact: {impact}\n"
            f"Evaluation: {evaluation} · Engine choice: {item.best_san}"
            + (f"\nOpening status: {book}" if book else ""))
        alternatives=(f"\n\nOther near-equal choices: {', '.join(item.alternative_sans)}." if item.alternative_sans else "")
        explanation=review_text_for_viewer(item.explanation,item,self.player_color)
        alternative_reason=review_text_for_viewer(item.best_reason,item,self.player_color)
        self.detail.setText(explanation+(f"\n\nEngine comparison: {alternative_reason}" if item.best_san!=item.san and alternative_reason else "")+alternatives+(f"\n\nStrongest reply: {item.opponent_response}" if item.opponent_response else "")+(f"\n\nKey moment: {key_moment}" if key_moment else ""))
        self.detail.setToolTip(f"Coaching names {item.side} as the side that made this move.")
        prompt=review_text_for_viewer(review_reflection_prompt(item.events) or "",item,self.player_color)
        self.detail_prompt.setText("Think it through · "+prompt if prompt else "")
        self.detail_prompt.setVisible(bool(prompt))
        view_note=" · Green arrow marks the engine move before the played move." if self.review_show_best else " · Green arrow marks the opponent’s strongest reply." if self.review_show_reply else ""
        reply_note=f"\nOpponent reply line: {item.opponent_pv}" if item.opponent_pv else ""
        self.detail_line.setText(
            f"Stockfish line: {item.pv or 'Unavailable'}{reply_note}"
            f"\nAnalysis: {item.confidence}{view_note}")
        self.detail_line.setToolTip("Depth values show Stockfish's completed search depth before and after the move; deep confirmation is shown when available.")
        self.reply_view_btn.setEnabled(bool(item.opponent_move))
        self.pv_step_btn.setEnabled(bool(item.best_pv_moves));self.pv_step_btn.setText(
            ("Restart best line" if self.review_pv_index>=len(item.best_pv_moves) else "Next best-line move")
            if self.review_pv_active and self.review_pv_kind=="best" else "Step through best line")
        self.reply_pv_step_btn.setEnabled(bool(item.opponent_pv_moves));self.reply_pv_step_btn.setText(
            ("Restart reply line" if self.review_pv_index>=len(item.opponent_pv_moves) else "Next reply-line move")
            if self.review_pv_active and self.review_pv_kind=="reply" else "Step through reply line")
        self.evalbar.set_eval(item.eval_after)
    def start_review(self):
        if not self.history: QMessageBox.information(self,"Review game","Play a game or import a PGN first."); return
        if not self.engine_ready:
            if self.chesscom_batch_total:
                self.chesscom_review_wait_attempts+=1
                if self.chesscom_review_wait_attempts<40:
                    self.statusBar().showMessage("Waiting for Stockfish before continuing the Chess.com batch…",5000)
                    QTimer.singleShot(250,self.start_review);return
                self.chesscom_review_queue.clear();self.chesscom_batch_total=0;self.chesscom_batch_completed=0;self.chesscom_batch_started_at=None
                self._finish_chesscom_batch_progress()
            message=("Stockfish is taking longer than expected to start. Check STOCKFISH_PATH and try again; the app will continue if the engine connects." if self.engine_startup_timed_out else "Stockfish is still starting. Try again in a moment.")
            QMessageBox.warning(self,"Review game",message); return
        self.chesscom_review_wait_attempts=0
        if self.review_busy:return
        self.set_mode("review");self.review_token+=1; token=self.review_token
        batch_review=bool(self.chesscom_batch_total)
        budget=(CHESSCOM_BATCH_REVIEW_BUDGET if batch_review else float(self.analysis_level.currentData()))
        self.active_review_level=("Fast batch" if batch_review else self.analysis_level.currentText())
        self.analysis_level.setEnabled(False)
        self.worker.review_cancel_event.clear()
        self.review_busy=True; self.review_moves=[];self._render_history(); self.progress.setRange(0,len(self.history)); self.progress.setValue(0)
        self._update_syzygy_button()
        self.review_summary.setText("Reviewing both sides…"); self.review_btn.setText("Reviewing…");self.coach_review_btn.setText("Reviewing…")
        if self.chesscom_batch_total and self.review_progress_dialog:
            self.review_progress_dialog.begin_game()
        else:
            self.review_progress_dialog=ReviewProgressDialog(
                self,self.chesscom_batch_total,self.chesscom_batch_started_at)
            self.review_progress_dialog.setStyleSheet(self._stylesheet())
            self.review_progress_dialog.cancel_requested.connect(self.cancel_review)
            self.review_progress_dialog.show()
        self.review_request.emit((token,self.game_start_board.copy(stack=False),list(self.history),self.book_path,budget))

    def cancel_review(self):
        if not self.review_busy:return
        self.worker.review_cancel_event.set()
        if self.review_progress_dialog:self.review_progress_dialog.status.setText("Stopping after the current position; completed analysis will be kept.")

    def _finish_chesscom_batch_progress(self):
        if self.review_progress_dialog:
            self.review_progress_dialog.finish();self.review_progress_dialog=None
        self.chesscom_batch_started_at=None

    def practice_mistakes(self,start_ply=None,include_all=False):
        if not self.review_moves:
            QMessageBox.information(self,"Practice move","Run a review first to build practice positions.");return
        moves=self.review_moves if include_all else review_practice_moves(self.review_moves,self.player_color)
        if not include_all and not moves:
            QMessageBox.information(self,"Retry review moments","This review did not flag any mistakes or missed chances in your moves to retry.");return
        dialog=MistakePracticeDialog(self.game_start_board,moves,self.history,self,start_ply,include_all,self.player_color)
        dialog.setStyleSheet(self._stylesheet())
        dialog.analysis_requested.connect(self.practice_analysis_request.emit)
        self.worker.practice_analysis_done.connect(dialog.apply_engine_feedback)
        try:dialog.exec()
        finally:
            try:dialog.analysis_requested.disconnect(self.practice_analysis_request.emit)
            except (TypeError,RuntimeError):pass
            try:self.worker.practice_analysis_done.disconnect(dialog.apply_engine_feedback)
            except (TypeError,RuntimeError):pass

    def practice_current_mistake(self):
        if 1<=self.review_index<=len(self.review_moves):
            self.practice_mistakes(self.review_moves[self.review_index-1].ply,include_all=True)

    def explore_openings(self):
        if not self.engine_ready:
            message="Stockfish is taking longer than expected to start. Check STOCKFISH_PATH and try again." if self.engine_startup_timed_out else "Stockfish is still starting. Try again in a moment."
            QMessageBox.information(self,"Opening explorer",message); return
        position=self._display_board().copy()
        self.explorer_dialog=OpeningExplorer(position,self.book_path,self.explore_request.emit,self)
        self.explorer_dialog.setStyleSheet(self._stylesheet())
        self.explorer_dialog.show()

    def open_puzzles(self):
        if not self.engine_ready:
            message="Stockfish is taking longer than expected to start. Check STOCKFISH_PATH and try again." if self.engine_startup_timed_out else "Stockfish is still starting. Try again in a moment."
            QMessageBox.information(self,"Puzzle studio",message);return
        dialog=PuzzleDialog(self.board_theme,self);dialog.setStyleSheet(self._stylesheet());dialog.puzzle_requested.connect(self._request_puzzle)
        self.puzzle_dialog=dialog;dialog.exec()
        if self.puzzle_dialog is dialog:self.puzzle_dialog=None

    def _puzzle_source_positions(self):
        positions=[];seen=set()
        def collect(start,moves):
            board=start.copy(stack=False)
            for move in moves:
                if move not in board.legal_moves:break
                if board.fullmove_number>=7 and len(board.piece_map())>=12 and not board.is_game_over():
                    key=position_key(board)
                    if key not in seen:seen.add(key);positions.append(board.fen())
                board.push(move)
        collect(self.game_start_board,self.history)
        if self.profile_store:
            try:
                for record in self.profile_store.games()[:12]:
                    if not record.get("pgn"):continue
                    game=chess.pgn.read_game(io.StringIO(record["pgn"]))
                    if game is not None:collect(game.board(),list(game.mainline_moves()))
            except Exception as exc:self.statusBar().showMessage(f"Some saved games could not be searched for puzzles: {exc}",6000)
        if len(positions)>80:positions=random.sample(positions,80)
        return positions

    def _request_puzzle(self,request):
        if not self.puzzle_dialog:return
        dialog_token,skill,difficulty,challenge_mode,recent_themes=request;self.puzzle_token+=1
        self.puzzle_active=(self.puzzle_token,self.puzzle_dialog,dialog_token)
        source_fens=[] if challenge_mode else self._puzzle_source_positions()
        task=(self.puzzle_token,skill,difficulty,source_fens,challenge_mode,recent_themes)
        self.puzzle_request.emit(task)

    def _puzzle_progress(self,token,done,total):
        if not self.puzzle_dialog or not getattr(self,"puzzle_active",None):return
        active_token,dialog,dialog_token=self.puzzle_active
        if token==active_token and dialog is self.puzzle_dialog:dialog.update_progress(dialog_token,done,total)

    def _puzzle_done(self,token,puzzle,error):
        if not self.puzzle_dialog or not getattr(self,"puzzle_active",None):return
        active_token,dialog,dialog_token=self.puzzle_active
        if token==active_token and dialog is self.puzzle_dialog:dialog.show_puzzle(dialog_token,puzzle,error)

    def _explorer_done(self,payload):
        if self.explorer_dialog and self.explorer_dialog.isVisible():
            self.explorer_dialog.show_lines(payload)
    def _review_progress(self,token,done,total,item):
        if token!=self.review_token:return
        self.review_moves.append(item); self.progress.setValue(done)
        self.export_review_btn.setEnabled(True)
        if self.review_progress_dialog:
            self.review_progress_dialog.update_progress(done,total)
            if self.chesscom_batch_total:
                self.review_progress_dialog.update_batch_progress(
                    self.chesscom_batch_completed,done,total)
        if not self.chesscom_batch_total:
            self._render_history(); self._refresh_review_summary(True); self._sync_board()
        self.statusBar().showMessage(f"Reviewing position {done} of {total}…")
    def _review_done(self,token,items,cancelled):
        if token!=self.review_token:return
        batch_review=bool(self.chesscom_batch_total)
        review_start_ply=getattr(self,"_chesscom_review_start_ply",None)
        self._chesscom_review_start_ply=None
        if self.review_progress_dialog and not batch_review:
            self.review_progress_dialog.finish();self.review_progress_dialog=None
        self.review_busy=False; self.review_btn.setText("Review Game");self.coach_review_btn.setText("Review this game"); self.review_moves=list(items)
        self.analysis_level.setEnabled(True)
        self._update_syzygy_button()
        self.export_review_btn.setEnabled(bool(items))
        self.reviewing=False; self.review_index=0;self.review_pv_active=False;self.review_pv_index=0; self._render_history(); self._sync_board()
        complete=not cancelled and len(items)==len(self.history)
        if complete:self._save_review_to_profile(items)
        self._refresh_review_summary(not complete)
        outcome=("Full game review complete" if complete else
                 "Review cancelled · partial results shown" if cancelled else
                 "Review stopped early · partial results shown")
        self.statusBar().showMessage(outcome,10000)
        self._update_opening()
        if self.chesscom_batch_total:
            if complete:
                self.chesscom_batch_completed+=1
                if self.review_progress_dialog:
                    self.review_progress_dialog.update_batch_progress(self.chesscom_batch_completed)
                if self.chesscom_review_queue:
                    self.statusBar().showMessage(
                        f"Reviewed {self.chesscom_batch_completed} of {self.chesscom_batch_total} Chess.com games…",10000)
                    QTimer.singleShot(0,self._open_next_chesscom_review)
                else:
                    self.statusBar().showMessage(
                        f"Batch review complete · {self.chesscom_batch_completed} Chess.com games reviewed.",12000)
                    self.chesscom_batch_total=0;self.chesscom_batch_completed=0
                    self._finish_chesscom_batch_progress()
            else:
                self.chesscom_review_queue.clear();self.chesscom_batch_total=0;self.chesscom_batch_completed=0
                self._finish_chesscom_batch_progress()
                self.statusBar().showMessage("Chess.com batch stopped. Completed game reviews were saved.",10000)
            return
        if complete:
            if review_start_ply is not None:
                self.begin_move_review(review_start_ply);return
            result=self.game_result_override or (self.board.result() if self.board.is_game_over() else "*")
            summary=ReviewSummaryDialog(items,detect_opening(self.history,self.openings),result,self.player_color,self,
                                       find_opening_ply(self.history,self.openings),
                                       getattr(self,"active_review_level","Standard"),self.syzygy_active);summary.setStyleSheet(self._stylesheet())
            if summary.exec()==QDialog.DialogCode.Accepted:self.begin_move_review(summary.selected_ply or 1)

    def begin_move_review(self,start_ply=1):
        if not self.review_moves:return
        self.set_mode("review")
        self._clear_review_overlays();self.review_pv_active=False;self.review_pv_index=0;self.reviewing=True
        self.review_index=max(1,min(len(self.history),int(start_ply)));self.selected=None;self.best_move=None;self._render_history();self._sync_board();self._show_detail()
        first=self.review_index-1
        if first in self.visible_review_plies:self.review_table.selectRow(self.visible_review_plies.index(first))

    def _refresh_review_summary(self,partial=False):
        if not self.review_moves:
            self.review_summary.setText("Run a review to compare both sides and find the game’s turning points.");self.review_metric_white.metric.setText("—");self.review_metric_black.metric.setText("—");self.review_metric_turning.metric.setText("—");return
        rows=[]
        for side in ("White","Black"):
            theirs=[m for m in self.review_moves if m.side==side]
            if not theirs:
                rows.append(f"<b>{side}</b> — no moves analyzed")
                continue
            accuracy=review_accuracy(theirs)
            avg_loss=sum(min(m.loss,1500) for m in theirs)/len(theirs)
            counts={name:sum(1 for m in theirs if m.verdict==name) for name in ("Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracy","Mistake","Blunder","Book","Forced")}
            rows.append(f"<b>{side}</b> {accuracy:.0f}% · avg loss {avg_loss:.0f} cp · {counts['Best']} best · {counts['Excellent']} excellent · {counts['Good']} good · {counts['Miss']} missed chances · {counts['Inaccuracy']+counts['Mistake']+counts['Blunder']} errors · {counts['Brilliant']} brilliant · {counts['Great']} great · {counts['Book']} book · {counts['Forced']} forced")
        # Review labels are based on expected-points loss; use that same
        # game-impact measure to select the turning point shown to the player.
        worst=max(self.review_moves,key=lambda m:m.expected_loss)
        phase_scores=[]
        for phase in ("Opening","Middlegame","Endgame"):
            subset=[m for m in self.review_moves if m.phase==phase]
            if subset:phase_scores.append(f"{phase} {review_accuracy(subset):.0f}%")
        prefix="Partial · " if partial else ""
        rows.append(f"{prefix}Biggest game-impact swing: {worst.side} {worst.san} ({worst.verdict}, {worst.expected_loss*100:.1f} pp expected score lost) · " + " / ".join(phase_scores))
        self.review_summary.setText("<br>".join(rows))
        sides={side:[m for m in self.review_moves if m.side==side] for side in ("White","Black")}
        for side,card in (("White",self.review_metric_white),("Black",self.review_metric_black)):
            moves=sides[side]
            if moves:
                accuracy=review_accuracy(moves);errors=sum(m.verdict in ("Miss","Inaccuracy","Mistake","Blunder") for m in moves)
                card.metric.setText(f"{accuracy:.0f}% · {errors} errors")
            else:card.metric.setText("—")
        self.review_metric_turning.metric.setText(f"{worst.san} · {worst.expected_loss*100:.1f} pp")
        self.coach_overview.setText(player_review_overview(self.review_moves,self.player_color))
        self.coach_goal.setText("Next focus: "+personalized_review_coach(self.review_moves,self.player_color,self._saved_game_stats()))
        self.coach_opening.setText("Opening: "+detect_opening(self.history,self.openings)+" · Explore responses or retry the mistakes that mattered.")

    def _reset_review_surfaces(self,message):
        if self.review_speech:self.review_speech.stop()
        self._last_spoken_ply=None
        if self.speak_review_btn.isChecked():self.speak_review_btn.setChecked(False)
        self.review_summary.setText(message);self.review_metric_white.metric.setText("—");self.review_metric_black.metric.setText("—");self.review_metric_turning.metric.setText("—")
        self.detail_verdict.setText("READY");self.detail_verdict.setStyleSheet("");self.detail_move.setText("Choose a reviewed move");self.retry_move_btn.setVisible(False)
        self.detail_metrics.setText("Move quality, evaluation and engine alternatives will appear here.");self.detail.setText(message);self.detail_prompt.clear();self.detail_prompt.hide();self.detail_line.setText("");self.pv_step_btn.setEnabled(False);self.reply_pv_step_btn.setEnabled(False);self.reply_view_btn.setEnabled(False);self.export_review_btn.setEnabled(False)
        self.coach_overview.setText("Finish a game and run review to get a focused lesson plan.")
        self.coach_goal.setText("Next focus: complete a game for personalised feedback.")
        self.coach_opening.setText("Opening: "+detect_opening(self.history,self.openings))

    def _current_pgn_text(self,include_review=False):
        game=chess.pgn.Game.from_board(self.game_start_board.copy(stack=False));node=game
        game.headers["Event"]="Offline Chess"
        game.headers["White"]="You" if self.player_color==chess.WHITE else "Stockfish"
        game.headers["Black"]="Stockfish" if self.player_color==chess.WHITE else "You"
        game.headers["OfflineChessUserColor"]="White" if self.player_color==chess.WHITE else "Black"
        game.headers["OfflineChessPlayedLocally"]="1" if self.rating_eligible else "0"
        game.headers["Date"]=datetime.now().strftime("%Y.%m.%d")
        for move in self.history:node=node.add_variation(move)
        if include_review:annotate_review_pgn(game,self.history,self.review_moves,self.player_color)
        result=self.game_result_override or (self.board.result() if self.board.is_game_over() else "*")
        game.headers["Result"]=result
        return game.accept(chess.pgn.StringExporter(headers=True,variations=include_review,comments=include_review))+"\n"

    def export_review_pgn(self):
        if not self.review_moves:
            QMessageBox.information(self,"Export review PGN","Run at least a partial game review first.");return
        path,_=QFileDialog.getSaveFileName(self,"Export annotated PGN",str(APP_DIR/"review.pgn"),"PGN files (*.pgn)")
        if not path:return
        try:
            with open(path,"w",encoding="utf-8") as out:out.write(self._current_pgn_text(include_review=True))
            self.statusBar().showMessage("Annotated review PGN exported",5000)
        except Exception as exc:QMessageBox.critical(self,"Export failed",str(exc))

    def _save_review_to_profile(self,items):
        if not self.profile_store:return
        try:
            side="White" if self.player_color==chess.WHITE else "Black"
            user_moves=[m for m in items if m.side==side]
            if self.external_review_game_id:
                user_acc=review_accuracy(user_moves)
                profile_stats=self._review_stats(user_moves)
                performance=(predicted_performance_elo(
                    self.profile_store.chesscom_games(self.external_review_username))
                    if self.external_review_username else None)
                self.profile_store.set_chesscom_review(
                    self.external_review_game_id,True,user_acc,performance,
                    serialize_review_moves(items,self.syzygy_path if self.syzygy_active else ""),
                    json.dumps(profile_stats))
                self._saved_game_stats_cache=None
                return
            white=[m for m in items if m.side=="White"];black=[m for m in items if m.side=="Black"]
            result=self.game_result_override or (self.board.result() if self.board.is_game_over() else "*")
            user_result=None
            if self.rating_eligible and result in ("1-0","0-1","1/2-1/2"):
                points=1.0 if result==("1-0" if self.player_color==chess.WHITE else "0-1") else .5 if result=="1/2-1/2" else 0.0
                user_result=points
            user_acc=review_accuracy(user_moves)
            white_acc=review_accuracy(white)
            black_acc=review_accuracy(black)
            losses=[min(m.loss,1500) for m in user_moves]
            key_material=self.game_start_board.fen()+"|"+" ".join(move.uci() for move in self.history)+f"|{side}|{self.elo_slider.value()}|{result}"
            record={"game_key":hashlib.sha256(key_material.encode("utf-8")).hexdigest(),
                "played_at":datetime.now().astimezone().isoformat(timespec="minutes"),"result":result,
                "user_color":side,"user_result":user_result,
                "opponent_elo":self.elo_slider.value() if self.rating_eligible else None,
                "elo_approx":int(self.elo_slider.value()<MIN_ELO),"user_accuracy":user_acc,
                "white_accuracy":white_acc,"black_accuracy":black_acc,"plies":len(items),
                "opening":detect_opening(self.history,self.openings),
                "average_loss":sum(losses)/len(losses) if losses else None,
                "stats_json":json.dumps(self._review_stats(user_moves)),
                "pgn":self._current_pgn_text() if self.profile_store.full_pgns_enabled() else None,
                "review_json":serialize_review_moves(items,self.syzygy_path if self.syzygy_active else "") if self.profile_store.full_pgns_enabled() else None}
            self.profile_store.save_review(record)
            self._saved_game_stats_cache=None
            self.statusBar().showMessage("Review summary saved to your profile · full PGN storage is optional",8000)
        except Exception as exc:
            self.statusBar().showMessage(f"Could not save profile summary: {exc}",10000)

    def _saved_game_stats(self):
        if self._saved_game_stats_cache is not None:return self._saved_game_stats_cache
        stats=[]
        if self.profile_store:
            try:
                for game in self.profile_store.games():
                    try:
                        saved_stats=json.loads(game.get("stats_json") or "{}")
                        if (isinstance(saved_stats,dict)
                                and saved_stats.get("analysis_version")==REVIEW_ANALYSIS_VERSION
                                and saved_stats.get("syzygy_path","")==normalized_syzygy_path(
                                    self.syzygy_path if self.syzygy_active else "")):
                            stats.append(saved_stats)
                    except (TypeError,ValueError):continue
            except Exception:pass
            try:
                username=self.profile_store.get_setting("chesscom_username","")
                if username:
                    for game in self.profile_store.chesscom_games(username):
                        if not game.get("reviewed"):continue
                        try:saved_stats=json.loads(game.get("stats_json") or "{}")
                        except (TypeError,ValueError):continue
                        if (isinstance(saved_stats,dict)
                                and saved_stats.get("analysis_version")==REVIEW_ANALYSIS_VERSION):
                            stats.append(saved_stats)
            except Exception:pass
        self._saved_game_stats_cache=stats
        return stats

    def _review_stats(self,user_moves):
        phase_values={}
        for phase in ("Opening","Middlegame","Endgame"):
            phase_moves=[m for m in user_moves if m.phase==phase]
            if phase_moves:phase_values[phase]=review_accuracy(phase_moves)
        category_counts={name:sum(1 for m in user_moves if m.verdict==name) for name in ("Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracy","Mistake","Blunder","Book","Forced")}
        errors=[m for m in user_moves if m.verdict in ("Miss","Inaccuracy","Mistake","Blunder")]
        strength_labels={"fork":"Tactical forks found","skewer":"Skewers created",
                         "discovered_attack":"Discovered attacks used","discovered_check":"Discovered checks found",
                         "sound_sacrifice":"Sound sacrifices found","new_pin":"Pins created",
                         "opponent_hanging_piece":"Opponent pieces left hanging",
                         "removed_defender":"Defenders removed to win material",
                         "pv_material_gain":"Material won in the engine line",
                         "only_good_move":"Only good moves found",
                         "castling":"King safety improved by castling","central_control":"Central control improved",
                         "king_safety":"King safety improved","piece_activity":"Piece activity improved",
                         "development":"Pieces developed","promotion":"Promotions found","checkmate":"Checkmates delivered"}
        approved_verdicts={"Best","Excellent","Good","Great","Brilliant"}
        strength_themes={}
        for item in user_moves:
            if item.verdict not in approved_verdicts:continue
            for event in item.events:
                label=strength_labels.get(event.kind)
                if label and event.severity=="positive":strength_themes[label]=strength_themes.get(label,0)+1
        themes={};by_ply={m.ply:m for m in errors};board=self.game_start_board.copy(stack=False)
        for ply,played in enumerate(self.history,1):
            item=by_ply.get(ply);before=board.copy();board.push(played)
            if not item:continue
            event_labels={"lost_defender":"Piece left without its only defender","hanging_piece":"Undefended piece exposed",
                          "trapped_piece":"Piece had no safe retreat","missed_tactic":"Missed tactical opportunity",
                          "missed_plan":"Missed positional improvement",
                          "missed_mate":"Missed forced mate","missed_material":"Missed material win",
                          "back_rank_mate":"Back-rank checkmate allowed","pv_material_loss":"Material lost in engine line",
                          "new_pin":"Piece left pinned","king_exposure":"King safety weakened",
                          "pawn_structure":"Pawn-structure weakness","repeat_piece":"Repeated moves with one piece",
                          "restricted_piece":"Piece activity restricted"}
            for event in item.events:
                if event.kind in event_labels and event.severity in ("critical","warning","context"):
                    label=event_labels[event.kind];themes[label]=themes.get(label,0)+1
            forcing_best=False
            if item.best_move in before.legal_moves:
                best_after=before.copy();best_after.push(item.best_move)
                forcing_best=before.is_capture(item.best_move) or best_after.is_check()
            if forcing_best and item.best_move!=played:themes["Missed a forcing capture or check"]=themes.get("Missed a forcing capture or check",0)+1
            side_delta=(material_balance(board)-material_balance(before))*(1 if before.turn==chess.WHITE else -1)
            if side_delta<=-2:themes["Dropped material after an error"]=themes.get("Dropped material after an error",0)+1
            themes[f"{item.phase} errors"]=themes.get(f"{item.phase} errors",0)+1
        return {"analysis_version":REVIEW_ANALYSIS_VERSION,"phase_accuracy":phase_values,
                "syzygy_path":normalized_syzygy_path(self.syzygy_path if self.syzygy_active else ""),
                "move_counts":category_counts,"error_themes":themes,
                "strength_themes":strength_themes}

    def open_profile(self):
        if not self.profile_store:
            QMessageBox.warning(self,"Profile unavailable",f"Could not open local profile storage.\n\n{self.profile_error or ''}");return
        dialog=ProfileDialog(self.profile_store,self.open_saved_game,self.set_board_theme,self.set_board_presentation,self);dialog.setStyleSheet(self._stylesheet());dialog.exec();self._saved_game_stats_cache=None

    def set_board_theme(self,theme_key):
        if theme_key not in BOARD_THEMES:return
        self.board_theme=theme_key;ChessBoard.default_theme=theme_key
        save_error=None
        if self.profile_store:
            try:self.profile_store.set_setting("board_theme",theme_key)
            except Exception as exc:save_error=str(exc)
        for widget in QApplication.allWidgets():
            if isinstance(widget,ChessBoard):widget.set_theme(theme_key)
        message=f"Board style · {BOARD_THEMES[theme_key]['name']}"+(f" · Could not save: {save_error}" if save_error else " · saved locally")
        self.statusBar().showMessage(message,6000)

    def set_board_presentation(self,piece_style,show_coordinates,animations):
        if piece_style not in PIECE_STYLES:return
        self.piece_style=piece_style;self.show_coordinates=bool(show_coordinates);self.board_animations=bool(animations)
        ChessBoard.default_piece_style=self.piece_style;ChessBoard.default_show_coordinates=self.show_coordinates;ChessBoard.default_animations=self.board_animations
        save_error=None
        if self.profile_store:
            try:
                self.profile_store.set_setting("piece_style",self.piece_style)
                self.profile_store.set_setting("show_coordinates","1" if self.show_coordinates else "0")
                self.profile_store.set_setting("board_animations","1" if self.board_animations else "0")
            except Exception as exc:save_error=str(exc)
        for widget in QApplication.allWidgets():
            if isinstance(widget,ChessBoard):widget.set_presentation(self.piece_style,self.show_coordinates,self.board_animations)
        text=f"Board presentation · {PIECE_STYLES[self.piece_style]['name']} pieces"
        self.statusBar().showMessage(text+(f" · Could not save: {save_error}" if save_error else " · saved locally"),6000)

    def open_saved_game(self,saved_game):
        try:
            if isinstance(saved_game,dict) and "chesscom_batch" in saved_game:
                self.chesscom_review_queue=list(saved_game["chesscom_batch"])
                self.chesscom_batch_total=len(self.chesscom_review_queue);self.chesscom_batch_completed=0
                self.chesscom_batch_started_at=time.monotonic()
                self._open_next_chesscom_review();return
            chesscom_record=(saved_game.get("chesscom_game") if isinstance(saved_game,dict) else None)
            start_chesscom_review=bool(saved_game.get("start_review")) if isinstance(saved_game,dict) else False
            if isinstance(saved_game,dict):
                if chesscom_record:
                    pgn_text=prepare_chesscom_pgn(chesscom_record)
                    review_json=(None if start_chesscom_review or not chesscom_record.get("reviewed")
                                 else chesscom_record.get("review_json"))
                else:pgn_text=saved_game["pgn"];review_json=saved_game.get("review_json")
            else:
                pgn_text=saved_game;review_json=None
            game=chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:raise ValueError("Saved PGN could not be read")
            moves=list(game.mainline_moves());board=game.board();start_board=board.copy(stack=False)
            review_ply=(saved_game.get("review_ply") if isinstance(saved_game,dict) else None)
            try:self._chesscom_review_start_ply=max(1,min(len(moves),int(review_ply))) if review_ply is not None and moves else None
            except (TypeError,ValueError):self._chesscom_review_start_ply=None
            for move in moves:
                if move not in board.legal_moves:raise ValueError("Saved PGN contains an illegal move")
                board.push(move)
            self.review_token+=1;self.worker.review_cancel_event.set();self.review_busy=False;self.review_btn.setText("Review Game");self.analysis_level.setEnabled(True);self._update_syzygy_button()
            self.game_version+=1;self.game_start_board=start_board;self.history=moves;self.board=board;self.review_moves=[];self.reviewing=False;self.review_index=len(moves);self.last_move=moves[-1] if moves else None
            self.external_review_game_id=chesscom_record.get("game_id") if chesscom_record else None
            self.external_review_username=chesscom_record.get("username") if chesscom_record else None
            self.external_review_players=({side:(game.headers.get(side," ")
                                                  if game.headers.get(side," ").strip() not in ("","?") else side)
                                           for side in ("White","Black")}
                                          if chesscom_record else {})
            self.external_review_ratings=({side:(game.headers.get(f"{side}Elo","")
                                                  if str(game.headers.get(f"{side}Elo"," ")).isdigit() else "")
                                           for side in ("White","Black")}
                                          if chesscom_record else {})
            self.external_review_clocks=chesscom_pgn_clock_positions(game) if chesscom_record else []
            self.rating_eligible=game.headers.get("OfflineChessPlayedLocally")=="1"
            self.player_color=chess.WHITE if game.headers.get("OfflineChessUserColor","White")=="White" else chess.BLACK
            self.side_choice.blockSignals(True);self.side_choice.setCurrentIndex(0 if self.player_color==chess.WHITE else 1);self.side_choice.blockSignals(False)
            result=game.headers.get("Result","*");self.game_result_override=result if result in ("1-0","0-1","1/2-1/2") and not board.is_game_over() else None
            self.review_table.setRowCount(0);self.progress.setValue(0);self._reset_review_surfaces("Review this saved game to refresh its analysis.")
            cached_loaded=False
            if review_json:
                try:
                    cached=deserialize_review_moves(review_json,self.syzygy_path if self.syzygy_active else "");replay=start_board.copy(stack=False)
                    valid=len(cached)==len(moves)
                    for index,move in enumerate(moves):
                        if not valid:break
                        if cached[index].ply!=index+1 or cached[index].san!=replay.san(move):valid=False;break
                        if not cached[index].opponent_pv_moves and cached[index].opponent_pv:
                            after_played=replay.copy(stack=False);after_played.push(move)
                            cached[index].opponent_pv_moves=parse_review_san_line(after_played,cached[index].opponent_pv)
                        replay.push(move)
                    if not valid:
                        raise ValueError("Cached review does not match this game")
                    self.review_moves=cached;self._refresh_review_summary(False);cached_loaded=True
                    message="Opened saved game with its full review"
                except (ValueError,TypeError,KeyError,json.JSONDecodeError):
                    self.review_moves=[];self._refresh_review_summary(False)
                    message="Opened saved game; saved review is outdated or invalid—run a fresh review"
            else:message="Opened saved profile game · run a review to refresh its analysis"
            self._update_player_labels();self._render_history();self._sync_board();self._update_opening();self.statusBar().showMessage(message,6000)
            self.set_mode("review")
            if cached_loaded:self.begin_move_review(1)
            elif start_chesscom_review:QTimer.singleShot(0,self.start_review)
        except Exception as exc:
            if self.chesscom_batch_total:
                self.chesscom_review_queue.clear();self.chesscom_batch_total=0;self.chesscom_batch_completed=0
                self._finish_chesscom_batch_progress()
            QMessageBox.critical(self,"Could not open saved game",str(exc))

    def _open_next_chesscom_review(self):
        if not self.chesscom_review_queue:
            self.chesscom_batch_total=0;self.chesscom_batch_completed=0;self._finish_chesscom_batch_progress();return
        record=self.chesscom_review_queue.pop(0)
        self.chesscom_review_wait_attempts=0
        self.statusBar().showMessage(
            f"Reviewing Chess.com game {self.chesscom_batch_completed+1} of {self.chesscom_batch_total}…",10000)
        self.open_saved_game({"chesscom_game":record,"start_review":True})

    def undo(self):
        if not self.history:return
        self.review_token+=1;self.worker.review_cancel_event.set();self.review_busy=False;self.review_btn.setText("Review Game");self.analysis_level.setEnabled(True);self._update_syzygy_button()
        self.game_version+=1; self.reviewing=False; self.game_result_override=None; self.bot_busy=False
        remove=2 if len(self.history)>=2 else 1; del self.history[-remove:]
        self.board=self.game_start_board.copy(stack=False)
        for move in self.history:self.board.push(move)
        self.last_move=self.history[-1] if self.history else None; self.review_moves=[]; self.review_index=len(self.history)
        self._reset_review_surfaces("Review this game to refresh its analysis.");self._render_history(); self._sync_board(); self._after_move(); self._update_opening()
    def reset(self):
        self.review_token+=1; self.worker.review_cancel_event.set(); self.review_busy=False; self.review_btn.setText("Review Game");self.analysis_level.setEnabled(True);self._update_syzygy_button()
        self.external_review_game_id=None;self.external_review_username=None;self.external_review_players={};self.external_review_ratings={};self.external_review_clocks=[]
        self.chesscom_review_queue.clear();self.chesscom_batch_total=0;self.chesscom_batch_completed=0
        self.game_version+=1; self.game_start_board=chess.Board();self.board=self.game_start_board.copy(); self.history=[]; self.review_moves=[]; self.reviewing=False; self.review_index=0; self.last_move=None; self.best_move=None; self.selected=None; self.game_result_override=None; self.bot_busy=False; self.rating_eligible=True
        self.review_table.setRowCount(0); self.progress.setValue(0); self._reset_review_surfaces("Run a review to compare both sides and find the game’s turning points."); self.evalbar.set_eval(0)
        self.best_view_btn.setChecked(False)
        self._update_player_labels(); self.turn_label.setText("Your turn · " + ("White" if self.player_color else "Black")); self._sync_board(); self._update_opening(); self.statusBar().showMessage("New game started")
        if self.engine_ready and self.board.turn!=self.player_color:self._after_move()
    def import_pgn(self):
        path,_=QFileDialog.getOpenFileName(self,"Import PGN",str(APP_DIR),"PGN files (*.pgn);;All files (*)")
        if not path:return
        try:
            with open(path,"r",encoding="utf-8-sig") as handle: game=chess.pgn.read_game(handle)
            if game is None:raise ValueError("No game was found in this PGN")
            moves=list(game.mainline_moves()); board=game.board();start_board=board.copy(stack=False)
            for move in moves:
                if move not in board.legal_moves:raise ValueError("PGN contains an illegal move")
                board.push(move)
            self.review_token+=1; self.worker.review_cancel_event.set(); self.review_busy=False; self.review_btn.setText("Review Game");self.analysis_level.setEnabled(True);self._update_syzygy_button()
            self.external_review_game_id=None;self.external_review_username=None
            self.external_review_players={};self.external_review_ratings={};self.external_review_clocks=[]
            self.game_version+=1; self.game_start_board=start_board;self.history=moves; self.board=board; self.review_moves=[]; self.reviewing=False; self.review_index=len(moves); self.last_move=moves[-1] if moves else None
            self.rating_eligible=game.headers.get("OfflineChessPlayedLocally")=="1"
            if game.headers.get("OfflineChessUserColor") in ("White","Black"):
                self.player_color=chess.WHITE if game.headers["OfflineChessUserColor"]=="White" else chess.BLACK
                self.side_choice.blockSignals(True);self.side_choice.setCurrentIndex(0 if self.player_color==chess.WHITE else 1);self.side_choice.blockSignals(False)
            result=game.headers.get("Result","*"); self.game_result_override=result if result in ("1-0","0-1","1/2-1/2") and not board.is_game_over() else None
            self.review_table.setRowCount(0);self.progress.setValue(0);self._reset_review_surfaces("Review this game to calculate move quality and add it to your profile.")
            self._update_player_labels();self._render_history(); self._sync_board(); self._update_opening(); self.statusBar().showMessage(f"Imported {len(moves)} plies")
        except Exception as exc:QMessageBox.critical(self,"Import failed",str(exc))
    def export_pgn(self):
        if not self.history and not self.game_result_override:QMessageBox.information(self,"Export PGN","There are no moves to export."); return
        path,_=QFileDialog.getSaveFileName(self,"Export PGN",str(APP_DIR/"game.pgn"),"PGN files (*.pgn)")
        if not path:return
        try:
            with open(path,"w",encoding="utf-8") as out:out.write(self._current_pgn_text())
            self.statusBar().showMessage("PGN exported",5000)
        except Exception as exc:QMessageBox.critical(self,"Export failed",str(exc))
    def flip_board(self):self.board_widget.flipped=not self.board_widget.flipped; self.board_widget.update()
    def _update_opening(self):
        result=detect_opening(self.history,self.openings); self.opening_label.setText(result)
        if not self.review_moves:self.coach_opening.setText("Opening: "+result)
    def set_sound(self,on):self.sound_enabled=on
    def _play_sound(self,name):
        if not self.sound_enabled:return
        path=BUNDLE_DIR/"assets"/f"{name}.wav"
        try:
            if not path.is_file():path=self._generated_tone(name)
            if os.name=="nt":
                import winsound
                winsound.PlaySound(str(path),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
                return
            if QSoundEffect:
                effect=self.sounds.get(name)
                if effect is None:
                    effect=QSoundEffect(self); effect.setSource(QUrl.fromLocalFile(str(path))); effect.setVolume(.35); self.sounds[name]=effect
                effect.play(); return
            QApplication.beep()
        except Exception:
            if os.name=="nt":
                try:
                    import winsound
                    notes={"move":[(660,65)],"capture":[(520,65),(780,75)],"check":[(880,110)],"castle":[(523,65),(659,65)],"game_end":[(659,80),(523,80),(392,150)]}.get(name,[(660,65)])
                    threading.Thread(target=lambda:[winsound.Beep(freq,duration) for freq,duration in notes],daemon=True).start()
                    return
                except Exception:pass
            QApplication.beep()

    @staticmethod
    def _generated_tone(name):
        """Create gentle, distinct short WAV cues instead of the system alert beep."""
        notes={"move":[(660,.065)],"capture":[(523,.065),(784,.075)],"check":[(880,.12)],
               "castle":[(523,.065),(659,.075)],"game_end":[(659,.08),(523,.08),(392,.15)]}.get(name,[(660,.065)])
        folder=Path(tempfile.gettempdir())/"offline_chess_studio_sounds"; folder.mkdir(parents=True,exist_ok=True)
        destination=folder/f"{name}.wav"
        if destination.is_file():return destination
        rate=22050; frames=[]
        for freq,duration in notes:
            count=int(rate*duration)
            for i in range(count):
                t=i/rate; envelope=min(1.0,t/.008,(duration-t)/.018)
                sample=int(6200*max(0,envelope)*math.sin(2*math.pi*freq*t))
                frames.append(struct.pack("<h",sample))
            frames.extend([b"\x00\x00"]*int(rate*.018))
        with wave.open(str(destination),"wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(rate); sound.writeframes(b"".join(frames))
        return destination
    def closeEvent(self,event):
        if self.review_speech:self.review_speech.stop()
        self.worker.stop_event.set()
        self.shutdown_request.emit()
        self.thread.wait(2500)
        event.accept()


def main():
    app=QApplication(sys.argv); app.setApplicationName("Offline Chess")
    app.setStyle("Fusion")
    window=ChessWindow(); window.showFullScreen()
    return app.exec()


if __name__=="__main__":
    raise SystemExit(main())
