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
    raise SystemExit(f"Install PyQt6 first: pip install PyQt6 ({exc})") from exc

from app_updater import UpdateCheckWorker, UpdateDownloadWorker, launch_windows_update
from offline_chess.config import *  # Re-export runtime settings for legacy imports.
from offline_chess.engine_worker import EngineWorker
from offline_chess.review_logic import *  # Re-export review API for legacy imports.
from offline_chess.chesscom_data import *  # Re-export Chess.com data API for legacy imports.
from offline_chess.chesscom_data import ChessComFetchWorker as _ChessComFetchWorker
from offline_chess.ui.charts import *  # Re-export chart widgets for legacy imports.
from offline_chess.ui.board import *  # Re-export board widgets for legacy imports.
from offline_chess.ui.common import fit_dialog_to_screen
from offline_chess.ui.theme import *  # Re-export visual style constants for legacy imports.
from offline_chess.ui.review_dialogs import *  # Re-export review dialogs for legacy imports.
from offline_chess.ui.profile_dialog import ProfileDialog
from offline_chess.ui.main_window import ChessWindow
from offline_chess.openings import (
    Opening,
    detect_opening,
    discover_lichess_files,
    find_opening_ply,
    load_openings,
    local_opening_exit_depth,
    opening_from_file,
    parse_pgn_line,
    position_key,
)

class ChessComFetchWorker(_ChessComFetchWorker):
    """Legacy entry point that keeps main-module request mocking injectable."""
    def __init__(self,operation,username,archive_url="",opponents=None,parent=None,
                 known_game_ids=None,minimum_archive_month=""):
        super().__init__(operation,username,archive_url,opponents,parent,known_game_ids,
                         minimum_archive_month,request_func=chesscom_request_json)




def main():
    app=QApplication(sys.argv); app.setApplicationName("Offline Chess")
    app.setStyle("Fusion")
    window=ChessWindow(); window.showFullScreen()
    return app.exec()


if __name__=="__main__":
    raise SystemExit(main())
