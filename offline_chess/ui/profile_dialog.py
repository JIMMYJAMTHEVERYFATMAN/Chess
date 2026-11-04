"""Chess.com profile, archive, and local game library dialog."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter, OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import chess
import chess.pgn
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, pyqtSlot, QObject, QPointF, QRectF, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QAction, QPolygonF, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QSlider,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QProgressBar, QGraphicsDropShadowEffect,
    QCheckBox, QSpinBox, QComboBox, QAbstractItemView, QScrollArea, QSizePolicy,
    QStackedWidget, QToolTip, QMenu, QTabWidget, QLineEdit,
)

from ..chesscom_data import *
from ..pgn_stats import PGN_STATS_VERSION, aggregate_pgn_stats
from ..openings import *
from ..review_logic import *
from .board import *
from .charts import *
from .feedback_dialog import FeedbackDialog
from .review_dialogs import *
from .theme import *

class ProfileDialog(QWidget):
    def __init__(self,store,open_game_callback,theme_changed_callback=None,presentation_changed_callback=None,parent=None,
                 sound_changed_callback=None,load_opening_callback=None,load_book_callback=None):
        super().__init__(parent);self.store=store;self.open_game_callback=open_game_callback;self.theme_changed_callback=theme_changed_callback;self.presentation_changed_callback=presentation_changed_callback
        root_layout=QVBoxLayout(self);self.tabs=QTabWidget();root_layout.addWidget(self.tabs)
        self._profile_loading_tasks={};self._build_loading_overlay()
        overview_page=QWidget();layout=QVBoxLayout(overview_page);self.tabs.addTab(overview_page,"Overview")
        self.rating=QLabel();self.rating.setObjectName("Brand");layout.addWidget(self.rating)
        self.rating_note=QLabel();self.rating_note.setObjectName("Muted");layout.addWidget(self.rating_note)
        try:puzzle_high_score=max(0,int(store.get_setting("puzzle_challenge_high_score","0") or 0))
        except (TypeError,ValueError):puzzle_high_score=0
        self.puzzle_high_score=QLabel(f"Puzzle challenge high score: {puzzle_high_score} puzzles")
        self.puzzle_high_score.setObjectName("AccentText");layout.addWidget(self.puzzle_high_score)
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
        self._build_settings_page(sound_changed_callback,load_opening_callback,load_book_callback)
        self._build_chesscom_page()
        self.tabs.currentChanged.connect(self._profile_page_changed)
        self.refresh()

    def _build_loading_overlay(self):
        self.profile_loading_overlay=QFrame(self.tabs);self.profile_loading_overlay.setObjectName("ProfileLoadingOverlay")
        self.profile_loading_overlay.setStyleSheet("QFrame#ProfileLoadingOverlay { background: rgba(20,22,24,175); }")
        self.profile_loading_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        overlay_layout=QVBoxLayout(self.profile_loading_overlay);overlay_layout.addStretch(1)
        self.profile_loading_frame=QFrame();self.profile_loading_frame.setObjectName("ProfileLoadingFrame")
        self.profile_loading_frame.setStyleSheet("QFrame#ProfileLoadingFrame { background: #242729; border: 1px solid #b5e08e; border-radius: 12px; }")
        frame_layout=QVBoxLayout(self.profile_loading_frame);frame_layout.setContentsMargins(24,14,24,14)
        self.profile_loading_label=QLabel("Loading profile…");self.profile_loading_label.setObjectName("AccentText")
        self.profile_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter);frame_layout.addWidget(self.profile_loading_label)
        overlay_layout.addWidget(self.profile_loading_frame,0,Qt.AlignmentFlag.AlignCenter);overlay_layout.addStretch(1)
        shadow=QGraphicsDropShadowEffect(self.profile_loading_frame);shadow.setBlurRadius(10)
        shadow.setColor(QColor(181,224,142,190));shadow.setOffset(0,0);self.profile_loading_frame.setGraphicsEffect(shadow)
        self.profile_loading_animation=QPropertyAnimation(shadow,b"blurRadius",self)
        self.profile_loading_animation.setDuration(900);self.profile_loading_animation.setStartValue(8.0)
        self.profile_loading_animation.setKeyValueAt(.5,26.0);self.profile_loading_animation.setEndValue(8.0)
        self.profile_loading_animation.setEasingCurve(QEasingCurve.Type.InOutSine);self.profile_loading_animation.setLoopCount(-1)
        self.profile_loading_overlay.hide()

    def begin_profile_loading(self,message="Loading profile…"):
        token=object();self._profile_loading_tasks[token]=(time.monotonic(),str(message))
        self.profile_loading_label.setText(str(message));self.profile_loading_overlay.setGeometry(self.tabs.rect())
        self.profile_loading_overlay.show();self.profile_loading_overlay.raise_()
        if self.profile_loading_animation.state()!=QPropertyAnimation.State.Running:self.profile_loading_animation.start()
        return token

    def end_profile_loading(self,token):
        if token not in self._profile_loading_tasks:return
        started,_message=self._profile_loading_tasks[token]
        remaining=max(0,int(220-(time.monotonic()-started)*1000))
        if remaining:
            QTimer.singleShot(remaining,lambda:self._finish_profile_loading(token,started))
        else:self._finish_profile_loading(token,started)

    def _finish_profile_loading(self,token,started):
        current=self._profile_loading_tasks.get(token)
        if current is None or current[0]!=started:return
        self._profile_loading_tasks.pop(token,None)
        if self._profile_loading_tasks:
            self.profile_loading_label.setText(list(self._profile_loading_tasks.values())[-1][1])
        else:
            self.profile_loading_animation.stop();self.profile_loading_overlay.hide()

    def _profile_page_changed(self,index):
        page_name=self.tabs.tabText(index) if index>=0 else "Profile"
        token=self.begin_profile_loading(f"Loading {page_name.lower()}…")
        QTimer.singleShot(0,lambda:self._finish_profile_page_change(index,token))

    def _finish_profile_page_change(self,index,token):
        try:
            if index==0:self.refresh()
            elif index==self.tabs.indexOf(self.chesscom_scroll):
                if not self._chesscom_data_loaded:self._load_chesscom_on_tab(index)
                else:self.refresh_chesscom_library(self.chesscom_username.text())
        finally:self.end_profile_loading(token)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"profile_loading_overlay"):
            self.profile_loading_overlay.setGeometry(self.tabs.rect())

    def _build_settings_page(self,sound_changed_callback,load_opening_callback,load_book_callback):
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(20,18,20,18);layout.setSpacing(14)
        title=QLabel("SETTINGS");title.setObjectName("SectionTitle");layout.addWidget(title)

        audio_title=QLabel("GAMEPLAY");audio_title.setObjectName("SectionTitle");layout.addWidget(audio_title)
        self.sound_toggle=QCheckBox("Sound effects")
        self.sound_toggle.setChecked(self.store.get_setting("sound_effects_enabled","1")=="1")
        self.sound_toggle.toggled.connect(lambda enabled:self._set_sound(enabled,sound_changed_callback))
        layout.addWidget(self.sound_toggle)
        sound_note=QLabel("Play short sound cues for moves, captures, checks, castling, and game results.")
        sound_note.setObjectName("Muted");sound_note.setWordWrap(True);layout.addWidget(sound_note)

        database_title=QLabel("LOCAL DATABASES");database_title.setObjectName("SectionTitle");layout.addWidget(database_title)
        database_row=QHBoxLayout()
        self.load_openings_btn=QPushButton("Load Lichess DB")
        self.load_openings_btn.setToolTip("Choose the Lichess opening TSV files to use for local opening names and analysis.")
        self.load_openings_btn.setEnabled(load_opening_callback is not None)
        self.load_openings_btn.clicked.connect(lambda:self._run_database_loader(load_opening_callback))
        database_row.addWidget(self.load_openings_btn)
        self.load_book_btn=QPushButton("Load weighted book")
        self.load_book_btn.setToolTip("Choose a local Polyglot .bin book for opening suggestions and review context.")
        self.load_book_btn.setEnabled(load_book_callback is not None)
        current_book=getattr(self._owner_window(),"book_path",None)
        if current_book:
            self.load_book_btn.setText("Weighted book loaded")
            self.load_book_btn.setToolTip(f"Loaded weighted opening book: {Path(current_book).name}")
        self.load_book_btn.clicked.connect(lambda:self._load_weighted_book(load_book_callback))
        database_row.addWidget(self.load_book_btn);database_row.addStretch();layout.addLayout(database_row)

        feedback_title=QLabel("FEEDBACK");feedback_title.setObjectName("SectionTitle");layout.addWidget(feedback_title)
        feedback_row=QHBoxLayout()
        self.report_bug_btn=QPushButton("Report a bug")
        self.report_bug_btn.setObjectName("FeedbackButton")
        self.report_bug_btn.setToolTip("Send a bug report to the app maintainer.")
        self.report_bug_btn.clicked.connect(lambda:self._send_feedback("bug"));feedback_row.addWidget(self.report_bug_btn)
        self.suggest_feature_btn=QPushButton("Suggest a feature")
        self.suggest_feature_btn.setObjectName("FeedbackButton")
        self.suggest_feature_btn.setToolTip("Send a feature suggestion to the app maintainer.")
        self.suggest_feature_btn.clicked.connect(lambda:self._send_feedback("feature"));feedback_row.addWidget(self.suggest_feature_btn)
        feedback_row.addStretch();layout.addLayout(feedback_row)
        self.feedback_status=QLabel("");self.feedback_status.setObjectName("Muted");self.feedback_status.setWordWrap(True);layout.addWidget(self.feedback_status)

        privacy_title=QLabel("DATA SHARING & PRIVACY");privacy_title.setObjectName("SectionTitle");layout.addWidget(privacy_title)
        self.usage_reporting_toggle=QCheckBox("Share usage check-ins with update checks")
        self.usage_reporting_toggle.setChecked(self.store.get_setting("usage_reporting_enabled","1")=="1")
        self.usage_reporting_toggle.toggled.connect(self._set_usage_reporting)
        layout.addWidget(self.usage_reporting_toggle)
        self.feedback_note=QLabel("When enabled, a check-in is sent at launch alongside the update check. It contains a random install ID, app version, last-seen time, and saved Chess.com username. A count-only notice of new check-ins is sent to the maintainer’s Discord. This setting applies to check-ins that have not started; an in-progress check-in cannot be recalled. Automatic update checks still run when sharing is off. Bug reports and feature suggestions are sent only when you submit them, and include your message, app version, install ID, and saved Chess.com username. Discord receives only the count of new feedback submissions, not message contents. Game PGNs are not attached. The service provider may process network connection details.")
        self.feedback_note.setObjectName("Muted");self.feedback_note.setWordWrap(True);layout.addWidget(self.feedback_note)

        wipe_title=QLabel("DELETE SAVED GAME DATA");wipe_title.setObjectName("SectionTitle");layout.addWidget(wipe_title)
        self.clear_all_btn=QPushButton("Delete all saved game data")
        self.clear_all_btn.setToolTip("Remove saved game summaries, reviews, downloaded Chess.com games, and their local PGN files.")
        self.clear_all_btn.clicked.connect(self.clear_all_data);layout.addWidget(self.clear_all_btn)
        wipe_note=QLabel("After confirmation, this removes local game summaries, reviews, downloaded Chess.com game records and PGNs, plus the puzzle challenge high score. App preferences and the saved Chess.com username remain.")
        wipe_note.setObjectName("Muted");wipe_note.setWordWrap(True);layout.addWidget(wipe_note)
        layout.addStretch(1)
        scroll.setWidget(page);self.settings_scroll=scroll;self.tabs.addTab(scroll,"Settings")

    def _set_sound(self,enabled,callback):
        if callback:
            callback(enabled)
        else:
            self.store.set_setting("sound_effects_enabled","1" if enabled else "0")

    def _owner_window(self):
        parent=self.parent()
        while parent is not None:
            if hasattr(parent,"main_tabs"):return parent
            parent=parent.parent()
        return None

    def _run_database_loader(self,callback):
        if callback:callback(self)

    def _load_weighted_book(self,callback):
        if not callback:return
        callback(self)
        book_path=getattr(self._owner_window(),"book_path",None)
        if book_path:
            self.load_book_btn.setText("Weighted book loaded")
            self.load_book_btn.setToolTip(f"Loaded weighted opening book: {Path(book_path).name}")

    def _set_usage_reporting(self,enabled):
        self.store.set_setting("usage_reporting_enabled","1" if enabled else "0")

    def _send_feedback(self,category):
        dialog=FeedbackDialog(self.store,category,self)

        def update_note(ok,_error):
            self.feedback_status.setText("Thanks. Your feedback was sent to the maintainer dashboard."
                                         if ok else "The feedback could not be sent. Your message remains in the form for retry.")

        dialog.completed.connect(update_note)
        dialog.exec()

    def _load_chesscom_on_tab(self,index):
        if index!=self.tabs.indexOf(self.chesscom_scroll) or self._chesscom_data_loaded:return
        self._chesscom_data_loaded=True
        username=self.chesscom_username.text().strip()
        if not username:return
        self.chesscom_status.setText("Loading Chess.com insights and downloaded games…")
        self._chesscom_initial_loading_token=self.begin_profile_loading("Loading Chess.com…")
        QTimer.singleShot(0,lambda:self._finish_chesscom_initial_load(username))

    def _finish_chesscom_initial_load(self,username):
        try:
            self.refresh_chesscom_library(username)
            if self.chesscom_status.text()=="Loading Chess.com insights and downloaded games…":
                count=len(self._chesscom_all_games)
                noun="game" if count==1 else "games"
                self.chesscom_status.setText(f"Loaded {count:,} downloaded {noun}.")
        finally:
            self.end_profile_loading(getattr(self,"_chesscom_initial_loading_token",None))
            self._chesscom_initial_loading_token=None

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
        pgn_header=QHBoxLayout();pgn_title=QLabel("QUICK PGN INSIGHTS · NO ENGINE REVIEW");pgn_title.setObjectName("SectionTitle");pgn_header.addWidget(pgn_title,1)
        self.chesscom_pgn_stats_status=QLabel("Derived from saved PGNs; move times use [%emt] or Chess.com clock tags where available.")
        self.chesscom_pgn_stats_status.setObjectName("Muted");pgn_header.addWidget(self.chesscom_pgn_stats_status)
        self.chesscom_pgn_stats_rescan=QPushButton("Rescan");self.chesscom_pgn_stats_rescan.setToolTip("Recalculate local statistics from saved PGNs. No engine is used.")
        self.chesscom_pgn_stats_rescan.clicked.connect(self.rescan_chesscom_pgn_stats);pgn_header.addWidget(self.chesscom_pgn_stats_rescan)
        self.chesscom_pgn_details_toggle=QPushButton("Show breakdown");self.chesscom_pgn_details_toggle.setCheckable(True)
        self.chesscom_pgn_details_toggle.toggled.connect(self._toggle_chesscom_pgn_details);pgn_header.addWidget(self.chesscom_pgn_details_toggle)
        layout.addLayout(pgn_header)
        self.chesscom_pgn_stats_progress=QProgressBar();self.chesscom_pgn_stats_progress.setTextVisible(False)
        self.chesscom_pgn_stats_progress.setMaximumHeight(5);self.chesscom_pgn_stats_progress.setVisible(False);layout.addWidget(self.chesscom_pgn_stats_progress)
        pgn_grid=QGridLayout();pgn_grid.setHorizontalSpacing(6);pgn_grid.setVerticalSpacing(6)
        self.chesscom_pgn_quick_values={}
        for index,(key,title) in enumerate((
            ("length","AVG LENGTH"),("checks","CHECKS / GAME"),("your_checks","YOUR CHECKS"),
            ("your_captures","YOUR CAPTURES"),("opponent_captures","OPP. CAPTURES"),
            ("castling","CASTLING"),("promotions","PROMOTIONS"),("queenless","QUEENLESS"),
            ("repeated","REPEATED POS."),("finish","MATE / STALEMATE"),
            ("clock","CLOCK TAGS"),("elapsed","TIME / MOVE"))):
            card=QFrame();card.setObjectName("SidePanel");card.setToolTip("Calculated from saved PGN mainlines. Select Show breakdown for coverage and move-by-piece details.")
            card_layout=QVBoxLayout(card);card_layout.setContentsMargins(8,5,8,5);card_layout.setSpacing(1)
            caption=QLabel(title);caption.setObjectName("Muted");card_layout.addWidget(caption)
            value=QLabel("—");value.setObjectName("AccentText");value.setMinimumHeight(20);card_layout.addWidget(value)
            self.chesscom_pgn_quick_values[key]=value;pgn_grid.addWidget(card,index//7,index%7)
        layout.addLayout(pgn_grid)
        self.chesscom_pgn_stats=QTableWidget(0,3);self.chesscom_pgn_stats.setHorizontalHeaderLabels(["Measure","Result","Coverage / source"])
        self._configure_chesscom_table(self.chesscom_pgn_stats);self.chesscom_pgn_stats.setMaximumHeight(300)
        self.chesscom_pgn_stats.setVisible(False);layout.addWidget(self.chesscom_pgn_stats)
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
        self._chesscom_archives=[];self._chesscom_available_games=[];self._chesscom_worker=None;self._chesscom_known_game_ids=set();self._chesscom_latest_game_month="";self._castling_scan_worker=None;self._pgn_stats_worker=None;self._chesscom_pgn_stats_records=[];self._chesscom_pgn_stats_summary="";self._chesscom_castling_scan_records=[];self._chesscom_castling_scan_summary="";self._chesscom_download_errors=0;self._chesscom_review_cache={};self._chesscom_pgn_header_cache={};self._chesscom_opening_mastery_cache={};self._chesscom_book_positions_cache=(None,set());self._chesscom_opening_trend_data={}
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
        worker._profile_loading_token=self.begin_profile_loading("Loading Chess.com…")
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
        worker=self.sender()
        if worker is not self._chesscom_worker:return
        loading_token=getattr(worker,"_profile_loading_token",None)
        self._chesscom_worker=None;self.chesscom_username.setEnabled(True);self.chesscom_connect.setEnabled(True);self.chesscom_download_all.setEnabled(True)
        self.chesscom_update_downloads.setEnabled(bool(self._chesscom_known_game_ids))
        self.chesscom_month.setEnabled(True);self.chesscom_available.setEnabled(True)
        self.chesscom_download_selected.setEnabled(True);self.chesscom_download_month.setEnabled(True);self.chesscom_country_lookup.setEnabled(True)
        self.chesscom_progress.setVisible(False)
        self.chesscom_country_cancel.setVisible(False)
        self.end_profile_loading(loading_token)

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
        worker._profile_loading_token=self.begin_profile_loading("Scanning castling timing…")
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
        self.end_profile_loading(getattr(self.sender(),"_profile_loading_token",None))
        self.refresh_chesscom_library(self.chesscom_username.text())
        if self._chesscom_castling_scan_summary:
            self.chesscom_castling_scan_status.setText(self._chesscom_castling_scan_summary)

    def rescan_chesscom_pgn_stats(self):
        if self._pgn_stats_worker and self._pgn_stats_worker.isRunning():return
        self._start_chesscom_pgn_stats_scan(self._chesscom_pgn_stats_records)

    def _start_chesscom_pgn_stats_scan(self,records):
        records=list(records)
        if not records:return
        self.chesscom_pgn_stats_rescan.setEnabled(False)
        self.chesscom_pgn_stats_status.setText(f"Reading {len(records):,} saved PGNs for local statistics…")
        self.chesscom_pgn_stats_progress.setRange(0,len(records));self.chesscom_pgn_stats_progress.setValue(0)
        self.chesscom_pgn_stats_progress.setVisible(True)
        worker=ChessComPgnStatsWorker(records,self);self._pgn_stats_worker=worker
        worker._profile_loading_token=self.begin_profile_loading("Reading saved PGNs…")
        worker.progress.connect(self._chesscom_pgn_stats_progress)
        worker.scan_done.connect(self._chesscom_pgn_stats_done)
        worker.finished.connect(self._chesscom_pgn_stats_worker_finished)
        worker.finished.connect(worker.deleteLater);worker.start()

    def _chesscom_pgn_stats_progress(self,done,total):
        self.chesscom_pgn_stats_progress.setRange(0,max(1,total));self.chesscom_pgn_stats_progress.setValue(done)

    def _chesscom_pgn_stats_done(self,results):
        try:
            self.store.set_chesscom_pgn_stats(results)
            failed=sum(bool(summary.get("error")) for _,summary in results)
            self._chesscom_pgn_stats_summary=(f"Scanned {len(results):,} PGNs locally · {failed:,} unreadable" if failed
                                              else f"Scanned {len(results):,} PGNs locally · no engine review")
        except (OSError,sqlite3.Error,TypeError,ValueError) as exc:
            self._chesscom_pgn_stats_summary=f"Could not save PGN statistics: {exc}"

    def _chesscom_pgn_stats_worker_finished(self):
        if self.sender() is not self._pgn_stats_worker:return
        self._pgn_stats_worker=None;self.chesscom_pgn_stats_progress.setVisible(False)
        self.end_profile_loading(getattr(self.sender(),"_profile_loading_token",None))
        self.refresh_chesscom_library(self.chesscom_username.text())
        if self._chesscom_pgn_stats_summary:
            self.chesscom_pgn_stats_status.setText(self._chesscom_pgn_stats_summary)

    def _refresh_chesscom_pgn_stats(self,games):
        summary=aggregate_pgn_stats(games);self.chesscom_pgn_stats.setRowCount(0)
        figures={}
        for measure,value,coverage in summary["rows"]:
            figures[measure]=(value,coverage)
            row=self.chesscom_pgn_stats.rowCount();self.chesscom_pgn_stats.insertRow(row)
            for column,text in enumerate((measure,value,coverage)):
                self.chesscom_pgn_stats.setItem(row,column,QTableWidgetItem(str(text)))
        compact={
            "length":("Average game length",lambda value:value.removesuffix(" moves")),
            "checks":("Checks delivered",lambda value:value.removesuffix(" per game")),
            "your_checks":("Your checks",lambda value:value.removesuffix(" per game")),
            "your_captures":("Your captures",lambda value:value.split(" · ",1)[0].removesuffix(" per game")),
            "opponent_captures":("Opponent captures",lambda value:value.removesuffix(" per game")),
            "castling":("Your castling",lambda value:value.split(" · ",1)[0].removesuffix(" games")),
            "promotions":("Your promotions",lambda value:value.split(" · ",1)[0]),
            "queenless":("Queenless positions reached",lambda value:value),
            "repeated":("Games with a repeated position",lambda value:value),
            "finish":("PGN final position",lambda value:value.replace(" checkmate · "," / ").replace(" stalemate","")),
            "clock":("Your clock annotations",lambda value:value.split(" in ",1)[1].replace(" games","")+" games" if " in " in value else value),
            "elapsed":("Your time per move",lambda value:value.split(" · ",1)[0]),
        }
        for key,(measure,shorten) in compact.items():
            label=self.chesscom_pgn_quick_values[key];entry=figures.get(measure)
            if entry:
                full_value,coverage=entry;label.setText(shorten(full_value));label.setToolTip(f"{full_value}\n{coverage}")
            else:
                label.setText("—");label.setToolTip("No scanned PGNs in this filter")
        worker_running=bool(self._pgn_stats_worker and self._pgn_stats_worker.isRunning())
        self.chesscom_pgn_stats_rescan.setEnabled(bool(self._chesscom_pgn_stats_records) and not worker_running)
        if worker_running:
            self.chesscom_pgn_stats_status.setText("Reading saved PGNs for local statistics…")
        elif summary["valid"]:
            self.chesscom_pgn_stats_status.setText(
                f"{summary['valid']:,} valid PGNs in this filter · {summary['pending']:,} awaiting scan · {summary['errors']:,} unreadable")
        elif summary["total"]:
            self.chesscom_pgn_stats_status.setText(f"PGN scan pending for {summary['total']:,} games.")
        else:
            self.chesscom_pgn_stats_status.setText("Download standard-chess PGNs to calculate these statistics.")

    def _toggle_chesscom_pgn_details(self,visible):
        self.chesscom_pgn_stats.setVisible(visible)
        self.chesscom_pgn_details_toggle.setText("Hide breakdown" if visible else "Show breakdown")

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
        self._chesscom_pgn_stats_records=[game for game in available if game.get("rules") in ("chess","")
            and game.get("pgn_path")]
        self._refresh_chesscom_pgn_stats([game for game in selected_games if game.get("rules") in ("chess","")
                                          and game.get("pgn_path")])
        if not (self._pgn_stats_worker and self._pgn_stats_worker.isRunning()):
            pending=[game for game in self._chesscom_pgn_stats_records
                     if game.get("pgn_stats_version")!=PGN_STATS_VERSION]
            if pending:self._start_chesscom_pgn_stats_scan(pending)
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
        openings=getattr(self._owner_window(),"openings",[])
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
            score_comparison=f"{score:.1f}% vs {prior_score:.1f}% ({score_change})" if score is not None and prior_score is not None else "unavailable"
            accuracy_comparison=(f"{average_accuracy:.1f}% vs {prior_average_accuracy:.1f}% ({accuracy_change})"
                                 if average_accuracy is not None and prior_average_accuracy is not None else "unavailable")
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
        self._open_game_and_return({"chesscom_game":records[0]})

    def review_selected_chesscom(self):
        records=self._selected_chesscom_records()
        if not records:self.chesscom_status.setText("Select one or more downloaded games to review.");return
        supported=[game for game in records if game["rules"] in ("chess","") and game["pgn_path"] and Path(game["pgn_path"]).is_file()]
        if not supported:
            reason=("Selected games use unsupported chess variants." if all(game["rules"] not in ("chess","") for game in records)
                    else "Selected games do not have stored PGN files. Download their month again to restore them.")
            self.chesscom_status.setText(reason);return
        if len(supported)!=len(records):self.chesscom_status.setText(f"Skipping {len(records)-len(supported)} game(s) without a stored standard-chess PGN.")
        self._open_game_and_return({"chesscom_batch":supported})

    def review_unreviewed_chesscom(self):
        try:rows=self.store.chesscom_games(self.chesscom_username.text())
        except ValueError:rows=[]
        records=[game for game in rows if not game["reviewed"] and game["rules"] in ("chess","")
                 and game["pgn_path"] and Path(game["pgn_path"]).is_file()]
        if not records:self.chesscom_status.setText("There are no downloaded, unreviewed standard-chess games.");return
        self._open_game_and_return({"chesscom_batch":records})

    def _open_game_and_return(self,saved_game):
        self.open_game_callback(saved_game)
        window=self._owner_window()
        if window is not None:window.main_tabs.setCurrentIndex(0)

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
        try:puzzle_high_score=max(0,int(self.store.get_setting("puzzle_challenge_high_score","0") or 0))
        except (TypeError,ValueError):puzzle_high_score=0
        self.puzzle_high_score.setText(f"Puzzle challenge high score: {puzzle_high_score} puzzles")
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
        self._open_game_and_return(self.records[row])

    def delete_selected(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.records):return
        game=self.records[row]
        answer=QMessageBox.question(self,"Delete game?","Delete this game and its saved review summary?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:self.store.delete_game(game["game_key"]);self.refresh()

    def clear_all_data(self):
        answer=QMessageBox.question(self,"Delete all saved game data?","Permanently delete local game summaries, reviews, downloaded Chess.com game records and PGN files, plus the puzzle challenge high score? App preferences and your saved Chess.com username will remain.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:
            self.store.clear_all();self.refresh();self.refresh_chesscom_library(self.chesscom_username.text())

    def clear_pgn_data(self):
        answer=QMessageBox.question(self,"Delete stored game moves?","Remove all saved PGNs? Review summaries and profile statistics will remain.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.store.clear_pgns();self.refresh();self.refresh_chesscom_library(self.chesscom_username.text())

    def shutdown_background_workers(self):
        if self._chesscom_worker and self._chesscom_worker.isRunning():self._chesscom_worker.cancel()
        if self._pgn_stats_worker and self._pgn_stats_worker.isRunning():
            self._pgn_stats_worker.requestInterruption();self._pgn_stats_worker.wait(2000)
        if self._castling_scan_worker and self._castling_scan_worker.isRunning():
            self._castling_scan_worker.requestInterruption();self._castling_scan_worker.wait(2000)

    def closeEvent(self,event):
        self.shutdown_background_workers()
        super().closeEvent(event)

__all__ = ["ProfileDialog"]
