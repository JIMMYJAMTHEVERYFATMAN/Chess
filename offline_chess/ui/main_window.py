"""Main application window and play/review orchestration."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import re
import struct
import sys
import tempfile
import threading
import time
import wave
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import chess
import chess.engine
import chess.pgn
import chess.polyglot
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, pyqtSlot, QObject, QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QAction, QPolygonF, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QSlider, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QProgressBar,
    QCheckBox, QSpinBox, QComboBox, QAbstractItemView, QScrollArea, QSizePolicy,
    QStackedWidget, QToolTip, QMenu, QTabWidget, QLineEdit,
)
try:
    from PyQt6.QtMultimedia import QSoundEffect
except ImportError:
    QSoundEffect = None
try:
    from PyQt6.QtTextToSpeech import QTextToSpeech
except ImportError:
    QTextToSpeech = None
try:
    import winsound
except ImportError:
    winsound = None

from app_updater import UpdateCheckWorker, UpdateDownloadWorker, launch_windows_update
from ..chesscom_data import *
from ..config import *
from ..engine_worker import EngineWorker
from ..openings import *
from ..review_logic import *
from ..telemetry import make_checkin
from .board import *
from .charts import *
from .feedback_dialog import FeedbackDialog
from .profile_dialog import ProfileDialog
from .review_dialogs import *
from .position_lab import PositionAnalysisPage
from .theme import *

def _unparsed_pgn_token(text):
    """Return the first non-PGN token ignored by python-chess, if any."""
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group())

    text = re.sub(r"\{.*?\}|;[^\r\n]*|^%[^\r\n]*", blank, text, flags=re.S | re.M)
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            continue
        remainder = chess.pgn.MOVETEXT_REGEX.sub("", line)
        remainder = re.sub(r"\d+\.{1,3}|\be\.p\.?\b|[+#]", " ", remainder, flags=re.I)
        token = re.search(r"\S+", remainder)
        if token:
            return token.group()
    return None

def _is_pgn_move_parse_error(error):
    return str(error).lower().startswith(("illegal san:", "invalid san:", "ambiguous san:"))

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
    position_analysis_request = pyqtSignal(object)
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
        self.openings: list[Opening] = []; self.opening_position_index = {}; self.book_path: Optional[str] = None
        self._closing=False;self._shutdown_requested=False;self._close_when_engine_stops=False
        self._opening_load_lock=threading.Lock()
        self.review_token=0; self.sound_enabled = True; self.sounds = {}; self.explorer_dialog=None
        self.review_progress_dialog=None
        self.puzzle_dialog=None;self.profile_page=None;self.profile_tab_placeholder=None;self._installing_profile_page=False;self.puzzle_token=0
        self.rating_eligible=True
        try:self.profile_store=ProfileStore();self.profile_error=None
        except Exception as exc:self.profile_store=None;self.profile_error=str(exc)
        if self.profile_store:
            try:self.sound_enabled=self.profile_store.get_setting("sound_effects_enabled","1")=="1"
            except Exception:pass
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
        self.evaluation_request.connect(self.worker.evaluate_position)
        self._load_default_openings(); self._sync_board()
        QTimer.singleShot(800,self._begin_usage_reporting)
        if IS_FROZEN and os.name == "nt":
            self._update_check_worker=None
            self._update_download_worker=None
            QTimer.singleShot(2500,self._check_for_updates)

    def _begin_usage_reporting(self):
        store=self.profile_store
        if not store:return
        if store.get_setting("usage_disclosure_seen")!="1":
            answer=QMessageBox.question(
                self,"Usage reporting is on by default",
                "Offline Chess can send a check-in at launch alongside its automatic update check so its maintainer can see active installations. "
                "It sends a random install ID, app version, last-seen time, and your saved Chess.com username. "
                "It does not send games, PGNs, moves, or your computer name. A count-only notice of new check-ins is sent to "
                "the maintainer’s Discord without those details. The hosting provider may process network connection details. "
                "You can turn reporting off in "
                "Profile → Settings at any time. Continue with reporting enabled?",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            store.set_setting("usage_disclosure_seen","1")
            store.set_setting("usage_reporting_enabled","1" if answer==QMessageBox.StandardButton.Yes else "0")
        # The launch check-in is sent from the same background worker as the update check.

    def _check_for_updates(self):
        if self._update_check_worker and self._update_check_worker.isRunning():return
        checkin=(make_checkin(self.profile_store)
                 if self.profile_store and self.profile_store.get_setting("usage_reporting_enabled","1")=="1"
                 else None)
        self._update_check_worker=UpdateCheckWorker(APP_VERSION,checkin,self)
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
        self.position_analysis_request.connect(self.worker.analyze_position)
        self.puzzle_request.connect(self.worker.generate_puzzle)
        self.syzygy_request.connect(self.worker.configure_syzygy)
        self.shutdown_request.connect(self.worker.shutdown)
        self.worker.ready.connect(self._engine_started); self.worker.failed.connect(self._engine_failed)
        self.worker.bot_move.connect(self._bot_move); self.worker.best_move_checked.connect(self._best_move_checked)
        self.worker.position_eval_checked.connect(self._position_eval_checked)
        self.worker.review_progress.connect(self._review_progress); self.worker.review_done.connect(self._review_done)
        self.worker.explore_done.connect(self._explorer_done)
        self.worker.position_analysis_done.connect(self.position_lab.apply_results)
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
        self.profile_btn=QPushButton("Profile"); self.profile_btn.clicked.connect(self.open_profile); top.addWidget(self.profile_btn)
        self.fullscreen_btn=QPushButton("Exit fullscreen");self.fullscreen_btn.setToolTip("F11 toggles full screen · Esc returns to windowed mode");self.fullscreen_btn.clicked.connect(self.toggle_fullscreen);top.addWidget(self.fullscreen_btn)

        self.main_tabs=QTabWidget();self.main_tabs.setObjectName("MainTabs");root.addWidget(self.main_tabs,1)
        game_page=QWidget();game_page_layout=QVBoxLayout(game_page);game_page_layout.setContentsMargins(0,0,0,0)
        splitter=QSplitter(Qt.Orientation.Horizontal); game_page_layout.addWidget(splitter,1); splitter.setChildrenCollapsible(False)
        play_area=QWidget(); play_col=QVBoxLayout(play_area); play_col.setContentsMargins(0,0,14,0); play_col.setSpacing(8)
        self.opponent=QFrame(); self.opponent.setObjectName("PlayerCard"); opp_row=QHBoxLayout(self.opponent);opp_row.setContentsMargins(20,14,20,14)
        self.opponent_avatar=QLabel("♟"); self.opponent_avatar.setObjectName("Avatar"); opp_row.addWidget(self.opponent_avatar)
        opp_info=QVBoxLayout(); self.opponent_name=QLabel("Stockfish 19"); self.opponent_name.setObjectName("PlayerName"); self.opponent_rating=QLabel("Opponent · 1320 Elo"); self.opponent_rating.setObjectName("Muted"); opp_info.addWidget(self.opponent_name); opp_info.addWidget(self.opponent_rating); opp_row.addLayout(opp_info); opp_row.addStretch()
        self.opponent_clock=QLabel("—:——");self.opponent_clock.setObjectName("ChessComClock");self.opponent_clock.setToolTip("Recorded Chess.com clock at this move");self.opponent_clock.setVisible(False);opp_row.addWidget(self.opponent_clock)
        play_col.addWidget(self.opponent)
        board_row=QHBoxLayout(); board_row.setSpacing(8); play_col.addLayout(board_row,1)
        self.evalbar=EvalBar(); board_row.addWidget(self.evalbar)
        self.board_widget=ChessBoard()
        self.board_widget.setMinimumWidth(360)  # Fit the board beside the side panel at the window's minimum width.
        self.board_widget.square_clicked.connect(self.on_square); board_row.addWidget(self.board_widget,1)
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
        foot=QHBoxLayout();foot.addStretch();self.flip_btn=self._button("Flip board","secondary");self.flip_btn.clicked.connect(self.flip_board);foot.addWidget(self.flip_btn);setup_col.addLayout(foot);play_col.addWidget(setup)
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
        self.main_tabs.addTab(game_page,"Game")
        self.position_lab=PositionAnalysisPage()
        self.position_lab.analysis_requested.connect(self.position_analysis_request.emit)
        self.main_tabs.addTab(self.position_lab,"Position Lab")
        self.puzzle_dialog=PuzzleDialog(self.board_theme,self,self.profile_store)
        self.puzzle_dialog.setStyleSheet(self._stylesheet())
        self.puzzle_dialog.puzzle_requested.connect(self._request_puzzle)
        self.puzzle_dialog.challenge_high_score_changed.connect(self._update_puzzle_high_score)
        self.main_tabs.addTab(self.puzzle_dialog,"Puzzles")
        self.profile_tab_placeholder=QWidget();placeholder_layout=QVBoxLayout(self.profile_tab_placeholder)
        placeholder_layout.addStretch(1);self.profile_placeholder_label=QLabel("Open your profile insights and saved games here.")
        self.profile_placeholder_label.setObjectName("Muted");self.profile_placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addWidget(self.profile_placeholder_label);placeholder_layout.addStretch(1)
        self.main_tabs.addTab(self.profile_tab_placeholder,"Profile")
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
        self.statusBar().showMessage("Offline play · your moves stay on this computer")
        if self.profile_error:self.statusBar().showMessage(f"Profile storage unavailable: {self.profile_error}")
        self.setStyleSheet(self._stylesheet())

    def _button(self,text,kind):
        names={"secondary":"Button","primary":"PrimaryButton","danger":"DangerButton","mode":"ModeButton"}
        btn=QPushButton(text); btn.setObjectName(names.get(kind,"Button")); btn.setCursor(Qt.CursorShape.PointingHandCursor); return btn

    def open_feedback(self,category):
        if not self.profile_store:
            QMessageBox.warning(self,"Feedback unavailable","Local profile storage is unavailable, so the feedback form cannot attach the required installation ID.")
            return
        dialog=FeedbackDialog(self.profile_store,category,self)
        dialog.completed.connect(self._feedback_completed)
        dialog.exec()

    def _feedback_completed(self,success,error):
        if success:
            self.statusBar().showMessage("Thanks. Your feedback was sent to the maintainer dashboard.",8000)
        else:
            self.statusBar().showMessage(f"Feedback could not be sent: {error or 'check your connection.'}",10000)
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
        QTabWidget#MainTabs::pane { border:1px solid #343a3d; border-radius:10px; top:-1px; }
        QTabWidget#MainTabs QTabBar::tab { background:#242729; color:#aab2b5; border:1px solid #343a3d; border-bottom:0; border-top-left-radius:8px; border-top-right-radius:8px; padding:10px 20px; margin-right:4px; font-weight:700; }
        QTabWidget#MainTabs QTabBar::tab:selected { background:#303a33; color:#b5e08e; border-color:#4a674e; }
        QTabWidget#MainTabs QTabBar::tab:hover { color:#eef2ed; }
        #Muted { color:#aab2b5; font-size:14px; }
        #OpeningPill { background:#292d30; color:#a9d7b4; border-radius:12px; padding:8px 12px; font-weight:600; }
        #PlayerCard,#SidePanel { background:#242729; border:1px solid #303639; border-radius:12px; }
        #InsetCard,#MetricCard,#ReviewCard { background:#1e2224; border:1px solid #343a3d; border-radius:10px; }
        #PositionPanel { background:#242729; border:1px solid #303639; border-radius:12px; }
        #PositionCard { background:#1e2224; border:1px solid #343a3d; border-radius:9px; }
        #PositionPanelTitle { color:#eef0f1; font-size:17px; font-weight:800; }
        #PieceRack { background:transparent; border:0; }
        QToolButton[pieceButton="true"] { background:#202426; color:#e6e9eb; border:1px solid #343a3d; border-radius:8px; font-size:31px; min-width:48px; max-width:62px; min-height:42px; }
        QToolButton[pieceButton="true"]:hover { background:#2b3230; border-color:#668f68; }
        QToolButton[pieceButton="true"][selectedPiece="true"] { background:#385b41; border:2px solid #91c65b; }
        QToolButton#CastlingToggle { background:transparent; color:#d2d9d8; border:0; text-align:left; padding:5px 0; font-weight:700; }
        QToolButton#CastlingToggle:hover { color:#b5e08e; }
        QRadioButton { color:#d0d6d7; spacing:6px; padding:3px; }
        QRadioButton::indicator { width:15px; height:15px; }
        QRadioButton::indicator:unchecked { background:#292e31; border:1px solid #697276; border-radius:8px; }
        QRadioButton::indicator:checked { background:#91c65b; border:3px solid #385b41; border-radius:8px; }
        QTableWidget#PositionResults { border:0; border-radius:5px; background:#1e2224; alternate-background-color:#252a2c; }
        QTableWidget#PositionResults QHeaderView::section { background:#292e31; padding:7px 5px; font-size:10px; }
        QTableWidget#PositionResults::item { padding:5px 4px; }
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
        QComboBox,QSpinBox,QLineEdit,QTextEdit { background:#292e31; color:#e6e9eb; border:1px solid #42494c; border-radius:8px; padding:9px 11px; min-height:24px; }
        QComboBox:hover,QSpinBox:hover { border-color:#668f68; }
        QComboBox::drop-down { border:0; width:26px; }
        QComboBox QAbstractItemView { background:#292e31; color:#e6e9eb; border:1px solid #42494c; selection-background-color:#385b41; padding:5px; }
        QPushButton { background:#343a3d; color:#e6e9eb; border:1px solid #42494c; border-radius:8px; padding:12px 15px; font-weight:600; min-height:30px; }
        QPushButton:hover { background:#41494c; }
        QPushButton#Button { background:#343a3d; border:1px solid #42494c; border-radius:8px; padding:12px 15px; font-weight:600; }
        QPushButton#Button:hover { background:#41494c; }
        QPushButton#PrimaryButton { background:#81b64c; color:#162116; border:0; border-radius:8px; padding:13px 16px; font-weight:800; }
        QPushButton#PrimaryButton:hover { background:#91c55b; }
        QPushButton#FeedbackButton { background:#292f31; color:#d4e5d7; border:1px solid #46534a; border-radius:8px; padding:8px 9px; min-height:22px; font-size:12px; font-weight:700; }
        QPushButton#FeedbackButton:hover { background:#354239; border-color:#81b64c; }
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
        candidates=[];seen=set()
        for folder in (BUNDLE_DIR,BUNDLE_DIR/"openings",APP_DIR,APP_DIR/"openings"):
            for path in discover_lichess_files(folder):
                key=path.resolve()
                if key not in seen:seen.add(key);candidates.append(path)
        if candidates: self._load_paths_async(candidates)

    def load_opening_dialog(self,dialog_parent=None):
        folder=QFileDialog.getExistingDirectory(dialog_parent or self,"Choose the Lichess chess-openings folder",str(APP_DIR))
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
            with self._opening_load_lock:
                if not self._closing:self.openings_loaded.emit(entries,paths)
        thread=threading.Thread(target=load,daemon=True,name="Opening database loader")
        thread.start()
        return thread

    def _set_openings(self,entries,paths):
        self.openings=entries
        self.opening_position_index=index_openings(entries)
        self.opening_label.setText(f"{len(entries):,} Lichess openings" if entries else "Opening database not loaded")
        self.opening_label.setToolTip("\n".join(str(p) for p in paths))
        self.statusBar().showMessage(f"Loaded {len(entries):,} opening lines from Lichess TSV",5000)
        if paths and not entries:
            QMessageBox.warning(self,"No openings loaded","The selected TSV files were found, but no valid opening lines could be read.")
        self._update_opening()
        if self.profile_page is not None:
            self.profile_page.refresh_chesscom_library(self.profile_page.chesscom_username.text())

    def load_book_dialog(self,dialog_parent=None):
        path,_=QFileDialog.getOpenFileName(dialog_parent or self,"Load a weighted Polyglot opening book",str(APP_DIR),"Polyglot books (*.bin)")
        if not path:return
        try:
            with chess.polyglot.open_reader(path):
                pass
            self.book_path=path
            self.statusBar().showMessage(f"Loaded weighted book · {Path(path).name}",6000)
        except Exception as exc:
            QMessageBox.warning(self,"Could not load opening book",f"Choose a valid Polyglot .bin opening book.\n\n{exc}")

    def _engine_started(self,info):
        self.engine_startup_timer.stop();self.engine_startup_timed_out=False
        self.engine_ready=True; self.engine_status.setText("Stockfish 19 · ready"); self.engine_status.setStyleSheet("color:#91ca9c")
        self.position_lab.set_engine_ready(True)
        self.puzzle_dialog.set_engine_ready(True)
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
        self.position_lab.set_engine_error(message)
        self.puzzle_dialog.set_engine_ready(False)
        self.puzzle_dialog.status.setText(f"Stockfish is unavailable: {message}")
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
        version,position,move=result
        if version!=self.game_version or position!=self.board.fen():return
        self.bot_busy=False
        if move and move in self.board.legal_moves and not self._game_finished():
            self._push(move); self._after_move()
            if self.board.turn==self.player_color and not self._game_finished():self.statusBar().clearMessage()
    def request_best(self):
        if not self.engine_ready or self._game_finished() or self.reviewing:return
        self.statusBar().showMessage("Finding the best move…"); self.best_request.emit((self.game_version,self.board.copy()))
    def _best_move_checked(self,version,position,move):
        if version!=self.game_version or position!=self.board.fen():return
        self._best_move(move)
    def _position_eval_checked(self,position,score):
        if position==self.board.fen():self.evalbar.set_eval(score)
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
            turn="Your move" if self.board.turn==self.player_color else "Stockfish is thinking" if self.bot_busy else "Stockfish to move"
            self.play_hint.setText(f"{turn} · {len(self.history)} half-moves played · {detect_opening(self.history,self.openings,self.opening_position_index)}")
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
        opening_ply=find_opening_ply(self.history,self.openings,self.opening_position_index)
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
        self.main_tabs.setCurrentWidget(self.puzzle_dialog)
        if not self.engine_ready:
            message="Stockfish is taking longer than expected to start. Check STOCKFISH_PATH and try again." if self.engine_startup_timed_out else "Stockfish is still starting. Try again in a moment."
            self.puzzle_dialog.status.setText(message)

    def _puzzle_source_positions(self,mode="focus",source="games"):
        positions=[];seen=set();weakness_skill="Any tactic";weakness_phase=""
        def collect(start,moves,source_label="a saved game",error_plies=None):
            board=start.copy(stack=False)
            for ply,move in enumerate(moves,1):
                if move not in board.legal_moves:break
                if board.fullmove_number>=3 and len(board.piece_map())>=8 and not board.is_game_over():
                    key=position_key(board);phase=game_phase(board);is_error=bool(error_plies and ply in error_plies)
                    if mode!="personal" or error_plies is None or is_error:
                        if key not in seen:
                            seen.add(key);positions.append({"fen":board.fen(),"source":source_label,"phase":phase,
                                                           "move_number":board.fullmove_number,"mistake":is_error})
                board.push(move)
        if source=="games" and mode!="personal":
            collect(self.game_start_board,self.history,"the current game")
            if self.profile_store:
                try:
                    for record in self.profile_store.games()[:20]:
                        if not record.get("pgn"):continue
                        game=chess.pgn.read_game(io.StringIO(record["pgn"]))
                        if game is not None and not game.errors:collect(game.board(),list(game.mainline_moves()),"a saved game")
                except Exception as exc:self.statusBar().showMessage(f"Some saved games could not be searched for puzzles: {exc}",6000)
        if self.profile_store and (source=="games" or mode=="personal"):
            try:
                username=self.profile_store.get_setting("chesscom_username","")
                records=self.profile_store.chesscom_games(username) if username else []
                theme_counts=Counter();phase_counts=Counter();personal_positions=[]
                if mode=="personal":
                    for record in records:
                        if not record.get("reviewed"):continue
                        try:
                            stats=json.loads(record.get("stats_json") or "{}")
                            error_themes=stats.get("error_themes",{}) if isinstance(stats,dict) else {}
                            for theme,count in error_themes.items():theme_counts[theme]+=int(count)
                            for phase,count in error_themes.items():
                                if phase.endswith(" errors"):phase_counts[phase[:-7]]+=int(count)
                        except (TypeError,ValueError,AttributeError):pass
                if source=="games":
                    for record in records[:60]:
                        pgn_path=record.get("pgn_path")
                        if not pgn_path or not Path(pgn_path).is_file():continue
                        try:
                            with Path(pgn_path).open("r",encoding="utf-8",errors="replace") as handle:
                                game=chess.pgn.read_game(handle)
                            if game is None or game.errors:continue
                            error_plies=set()
                            if mode=="personal":
                                try:
                                    review=json.loads(record.get("review_json") or "{}")
                                    review_moves=review.get("moves",[]) if isinstance(review,dict) else []
                                    for move_info in review_moves:
                                        if (isinstance(move_info,dict) and move_info.get("side")==record.get("user_color")
                                                and move_info.get("verdict") in ("Miss","Inaccuracy","Mistake","Blunder")):
                                            error_plies.add(int(move_info.get("ply",0)))
                                except (TypeError,ValueError,AttributeError):pass
                            before_count=len(positions)
                            collect(game.board(),list(game.mainline_moves()),"a downloaded Chess.com game",
                                    error_plies if mode=="personal" else None)
                            if len(positions)>before_count:personal_positions.extend(positions[before_count:])
                        except (OSError,UnicodeError,ValueError):continue
                if mode=="personal" and source=="games":positions=personal_positions
                theme_map={"Missed forced mate":"Mate","King safety weakened":"Castling","Dropped material after an error":"Winning material",
                           "Missed material win":"Winning material","Undefended piece exposed":"Winning material",
                           "Piece left without its only defender":"Winning material","Missed tactical opportunity":"Any tactic"}
                mapped=Counter()
                for theme,count in theme_counts.items():
                    if theme in theme_map:mapped[theme_map[theme]]+=count
                weakness_skill=sorted(mapped,key=lambda key:(-mapped[key],key))[0] if mapped else "Any tactic"
                weakness_phase=sorted(phase_counts,key=lambda key:(-phase_counts[key],key))[0] if phase_counts else ""
                if mode=="personal" and source=="games" and not positions:
                    for record in records[:60]:
                        path=record.get("pgn_path")
                        if not path or not Path(path).is_file():continue
                        try:
                            with Path(path).open("r",encoding="utf-8",errors="replace") as handle:
                                game=chess.pgn.read_game(handle)
                            if game and not game.errors:collect(game.board(),list(game.mainline_moves()),"a downloaded Chess.com game")
                        except (OSError,UnicodeError,ValueError):continue
                    positions=[item for item in positions if item["source"]=="a downloaded Chess.com game"]
            except Exception as exc:self.statusBar().showMessage(f"Chess.com puzzle data could not be loaded: {exc}",6000)
        if source=="games" and len(positions)>160:
            positions=sorted(positions,key=lambda item:hashlib.sha256(item["fen"].encode()).digest())[:160]
        return positions,weakness_skill,weakness_phase

    def _request_puzzle(self,request):
        if not self.puzzle_dialog:return
        request=dict(request);dialog_token=request.get("token",0);self.puzzle_token+=1
        self.puzzle_active=(self.puzzle_token,self.puzzle_dialog,dialog_token)
        mode=request.get("mode","focus");source=request.get("source","generated")
        source_positions,personal_skill,personal_phase=(self._puzzle_source_positions(mode,source)
            if source=="games" or mode=="personal" else ([],"Any tactic",""))
        if source=="games" and not source_positions:
            self.puzzle_dialog.personal_skill=personal_skill;self.puzzle_dialog.personal_phase=personal_phase
            self.puzzle_dialog.show_puzzle(dialog_token,None,
                "No saved game positions are available yet. Import a PGN, play a game, or download Chess.com games in Profile.")
            return
        if mode=="personal":self.puzzle_dialog.personal_skill=personal_skill;self.puzzle_dialog.personal_phase=personal_phase
        request.update({"source":source,"source_positions":source_positions,"personal_skill":personal_skill,"personal_phase":personal_phase,
                        "skill":personal_skill if mode=="personal" else request.get("skill","Any tactic")})
        task=(self.puzzle_token,request)
        self.puzzle_request.emit(task)

    def _update_puzzle_high_score(self,score):
        self.puzzle_high_score=int(score)

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
            summary=ReviewSummaryDialog(items,detect_opening(self.history,self.openings,self.opening_position_index),result,self.player_color,self,
                                       find_opening_ply(self.history,self.openings,self.opening_position_index),
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
        self.coach_opening.setText("Opening: "+detect_opening(self.history,self.openings,self.opening_position_index)+" · Explore responses or retry the mistakes that mattered.")

    def _reset_review_surfaces(self,message):
        if self.review_speech:self.review_speech.stop()
        self._last_spoken_ply=None
        if self.speak_review_btn.isChecked():self.speak_review_btn.setChecked(False)
        self.review_summary.setText(message);self.review_metric_white.metric.setText("—");self.review_metric_black.metric.setText("—");self.review_metric_turning.metric.setText("—")
        self.detail_verdict.setText("READY");self.detail_verdict.setStyleSheet("");self.detail_move.setText("Choose a reviewed move");self.retry_move_btn.setVisible(False)
        self.detail_metrics.setText("Move quality, evaluation and engine alternatives will appear here.");self.detail.setText(message);self.detail_prompt.clear();self.detail_prompt.hide();self.detail_line.setText("");self.pv_step_btn.setEnabled(False);self.reply_pv_step_btn.setEnabled(False);self.reply_view_btn.setEnabled(False);self.export_review_btn.setEnabled(False)
        self.coach_overview.setText("Finish a game and run review to get a focused lesson plan.")
        self.coach_goal.setText("Next focus: complete a game for personalised feedback.")
        self.coach_opening.setText("Opening: "+detect_opening(self.history,self.openings,self.opening_position_index))

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
                "opening":detect_opening(self.history,self.openings,self.opening_position_index),
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

    def _ensure_profile_page(self):
        if self.profile_page is not None:return self.profile_page
        if not self.profile_store:
            self.profile_placeholder_label.setText("Profile storage is unavailable.")
            self.statusBar().showMessage(f"Profile storage unavailable: {self.profile_error or ''}",8000)
            return None
        placeholder=self.profile_tab_placeholder;index=self.main_tabs.indexOf(placeholder)
        if index<0:return None
        page=ProfileDialog(self.profile_store,self.open_saved_game,self.set_board_theme,self.set_board_presentation,self,
                           sound_changed_callback=self.set_sound,load_opening_callback=self.load_opening_dialog,
                           load_book_callback=self.load_book_dialog)
        page.setStyleSheet(self._stylesheet())
        self._installing_profile_page=True;signals_blocked=self.main_tabs.blockSignals(True)
        self.main_tabs.removeTab(index);self.main_tabs.insertTab(index,page,"Profile")
        self.profile_page=page;self.profile_tab_placeholder=None
        placeholder.deleteLater()
        self.main_tabs.blockSignals(signals_blocked);self._installing_profile_page=False
        return page

    def _main_tab_changed(self,index):
        widget=self.main_tabs.widget(index)
        if widget is self.profile_tab_placeholder:
            page=self._ensure_profile_page()
            if page is not None:self.main_tabs.setCurrentWidget(page)
        elif widget is self.profile_page and self.profile_page is not None and not self._installing_profile_page:
            self._schedule_profile_refresh(self.profile_page)

    def _schedule_profile_refresh(self,page):
        token=page.begin_profile_loading("Refreshing profile…")
        QTimer.singleShot(0,lambda:self._refresh_profile_page(page,token))

    def _refresh_profile_page(self,page,token):
        try:
            if page is self.profile_page and self.main_tabs.currentWidget() is page:
                page.refresh()
                page.refresh_chesscom_library(page.chesscom_username.text())
        finally:page.end_profile_loading(token)

    def open_profile(self):
        if not self.profile_store:
            QMessageBox.warning(self,"Profile unavailable",f"Could not open local profile storage.\n\n{self.profile_error or ''}");return
        page=self._ensure_profile_page()
        if page is None:return
        if self.main_tabs.currentWidget() is page:
            self._schedule_profile_refresh(page)
        self._saved_game_stats_cache=None
        self.puzzle_dialog.refresh_high_score()
        self.main_tabs.setCurrentWidget(page)

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
            if game.errors:raise ValueError(f"Saved PGN contains invalid moves: {game.errors[0]}")
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
        self.move_table.setRowCount(0); self.review_table.setRowCount(0); self.progress.setValue(0); self._reset_review_surfaces("Run a review to compare both sides and find the game’s turning points."); self.evalbar.set_eval(0)
        self.best_view_btn.setChecked(False)
        self._update_player_labels(); self.turn_label.setText("Your turn · " + ("White" if self.player_color else "Black")); self._sync_board(); self._update_opening(); self.statusBar().showMessage("New game started")
        if self.engine_ready and self.board.turn!=self.player_color:self._after_move()
    def import_pgn(self):
        path,_=QFileDialog.getOpenFileName(self,"Import PGN",str(APP_DIR),"PGN files (*.pgn);;All files (*)")
        if not path:return
        try:
            with open(path,"r",encoding="utf-8-sig") as handle:
                pgn_text=handle.read()
            pgn_source=io.StringIO(pgn_text); game=chess.pgn.read_game(pgn_source)
            imported_pgn_text=pgn_text[:pgn_source.tell()]
            if game is None:raise ValueError("No game was found in this PGN")
            blocking_errors=[error for error in game.errors if not _is_pgn_move_parse_error(error)]
            if blocking_errors:raise ValueError(f"PGN could not be read: {blocking_errors[0]}")
            moves=list(game.mainline_moves()); board=game.board();start_board=board.copy(stack=False)
            if not moves:raise ValueError("This PGN contains no moves; the current game was kept")
            for move in moves:
                if move not in board.legal_moves:raise ValueError("PGN contains an illegal move")
                board.push(move)
            move_error=next((error for error in game.errors if _is_pgn_move_parse_error(error)),None)
            unparsed_token=_unparsed_pgn_token(imported_pgn_text)
            warning=(f" · invalid token {unparsed_token!r}; loaded legal prefix only" if unparsed_token else
                     f" · {str(move_error).split(' while parsing ',1)[0]}; loaded legal prefix only" if move_error else "")
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
            self._update_player_labels();self.turn_label.setText(("Your turn" if board.turn==self.player_color else "Stockfish to move")+" · "+("White" if board.turn else "Black"))
            self._render_history(); self._sync_board(); self._update_opening(); self.statusBar().showMessage(f"Imported {len(moves)} plies{warning}")
        except Exception as exc:
            message="This file is not valid UTF-8 text." if isinstance(exc,UnicodeDecodeError) else str(exc)
            QMessageBox.critical(self,"Import failed",message)
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
        result=detect_opening(self.history,self.openings,self.opening_position_index); self.opening_label.setText(result)
        if not self.review_moves:self.coach_opening.setText("Opening: "+result)
    def set_sound(self,on):
        self.sound_enabled=bool(on)
        if self.profile_store:
            try:self.profile_store.set_setting("sound_effects_enabled","1" if on else "0")
            except Exception as exc:self.statusBar().showMessage(f"Sound setting could not be saved: {exc}",8000)
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
        with self._opening_load_lock:self._closing=True
        profile_page=getattr(self,"profile_page",None)
        if profile_page is not None:profile_page.shutdown_background_workers()
        if self.review_speech:self.review_speech.stop()
        self.worker.stop_event.set()
        if self.thread.isRunning():
            if not self._shutdown_requested:
                self._shutdown_requested=True
                self.shutdown_request.emit()
            if not self.thread.wait(2500):
                if not self._close_when_engine_stops:
                    self._close_when_engine_stops=True
                    self.thread.finished.connect(self.close)
                self.statusBar().showMessage("Stockfish is still stopping; the window will close when it finishes.")
                event.ignore()
                if not self.thread.isRunning():QTimer.singleShot(0,self.close)
                return
        event.accept()

__all__ = ["ChessWindow"]
