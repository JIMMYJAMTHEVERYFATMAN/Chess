"""Runtime paths and version settings shared by the app and packager."""
import os
import sys
from pathlib import Path
from .build_config import USAGE_REPORT_URL as BUILT_USAGE_REPORT_URL

APP_VERSION = "1.1.4"
IS_FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else BUNDLE_DIR
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", str(BUNDLE_DIR / ("stockfish.exe" if os.name == "nt" else "stockfish")))
MIN_ELO, MAX_ELO = 1320, 3190
MIN_TARGET_ELO = 400
CHESSCOM_BATCH_REVIEW_BUDGET = .08
# A release build embeds the public collector URL. Local runs can override it.
USAGE_REPORT_URL = os.environ.get("OFFLINE_CHESS_CHECKIN_URL", BUILT_USAGE_REPORT_URL)

__all__ = ["APP_VERSION", "IS_FROZEN", "BUNDLE_DIR", "APP_DIR", "STOCKFISH_PATH", "MIN_ELO", "MAX_ELO",
           "MIN_TARGET_ELO", "CHESSCOM_BATCH_REVIEW_BUDGET", "USAGE_REPORT_URL"]
