"""Opening repertoire study and Chess.com performance practice."""
import re
from collections import defaultdict

import chess
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QGridLayout,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtGui import QColor


class _OpeningPerformanceWorker(QThread):
    loaded = pyqtSignal(int, object)

    def __init__(self, store, generation, parent=None):
        super().__init__(parent)
        self.store = store
        self.generation = generation

    def run(self):
        records = []
        if self.store:
            try:
                username = self.store.get_setting("chesscom_username", "")
                if username:
                    records = self.store.chesscom_games(username)
            except Exception:
                records = []
        self.loaded.emit(self.generation, records)


class OpeningTrainerPage(QWidget):
    """Study both sides' opening lines and drill openings from saved games."""

    def __init__(self, profile_store, parent=None):
        super().__init__(parent)
        self.profile_store = profile_store
        self.openings = []
        self.lines = []
        self.practice_line = None
        self.practice_ply = 0
        self.practice_board = chess.Board()
        self.practice_active = False
        self.last_reply = ""
        self._grouped_lines = {}
        self._catalog_ready = False
        self._refresh_pending = False
        self._refresh_generation = 0
        self._choice_population = None
        self._records_worker = None
        self._records_reload_pending = False
        self.stats = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0})
        self.stats_by_eco = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0})
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title = QLabel("Opening Trainer")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        intro = QLabel("Study complete lines for both colors, then focus on openings you play often or struggle with.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Side to train"))
        self.side = QComboBox()
        self.side.addItem("White", chess.WHITE)
        self.side.addItem("Black", chess.BLACK)
        self.side.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.side)
        controls.addWidget(QLabel("Opening"))
        self.opening_choice = QComboBox()
        self.opening_choice.currentIndexChanged.connect(self._select_opening)
        controls.addWidget(self.opening_choice, 1)
        root.addLayout(controls)

        body = QHBoxLayout()
        self.performance = QTableWidget(0, 5)
        self.performance.setHorizontalHeaderLabels(["Opening", "Games", "W–D–L", "Win %", "Focus"])
        self.performance.verticalHeader().hide()
        self.performance.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.performance.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.performance.setAlternatingRowColors(True)
        self.performance.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            self.performance.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.performance.itemSelectionChanged.connect(self._performance_selected)
        body.addWidget(self.performance, 3)

        right = QVBoxLayout()
        self.summary = QLabel("Choose an opening to see its repertoire lines.")
        self.summary.setObjectName("ReviewSummary")
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        self.line_list = QTableWidget(0, 2)
        self.line_list.setHorizontalHeaderLabels(["Line", "Moves"])
        self.line_list.verticalHeader().hide()
        self.line_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.line_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.line_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.line_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        right.addWidget(self.line_list, 2)
        body.addLayout(right, 4)
        root.addLayout(body, 1)

        practice = QFrame()
        practice.setObjectName("MetricCard")
        practice_layout = QVBoxLayout(practice)
        practice_layout.setContentsMargins(16, 12, 16, 12)
        self.prompt = QLabel("Start a drill to practice your selected side's moves and learn the opponent's replies.")
        self.prompt.setObjectName("ReviewSummary")
        self.prompt.setWordWrap(True)
        practice_layout.addWidget(self.prompt)
        self.answers = QGridLayout()
        practice_layout.addLayout(self.answers)
        row = QHBoxLayout()
        self.frequent_btn = QPushButton("Practice most played")
        self.frequent_btn.clicked.connect(lambda: self.start_priority("frequent"))
        row.addWidget(self.frequent_btn)
        self.weak_btn = QPushButton("Practice lowest win rate")
        self.weak_btn.clicked.connect(lambda: self.start_priority("weak"))
        row.addWidget(self.weak_btn)
        self.start_btn = QPushButton("Practice selected opening")
        self.start_btn.clicked.connect(self.start_selected)
        row.addWidget(self.start_btn)
        practice_layout.addLayout(row)
        root.addWidget(practice)

        self.loading_overlay = QFrame(self)
        self.loading_overlay.setObjectName("OpeningLoadingOverlay")
        self.loading_overlay.setStyleSheet("QFrame#OpeningLoadingOverlay { background: rgba(20,22,24,175); }")
        self.loading_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.addStretch(1)
        self.loading_frame = QFrame()
        self.loading_frame.setObjectName("OpeningLoadingFrame")
        self.loading_frame.setStyleSheet("QFrame#OpeningLoadingFrame { background: #242729; border: 1px solid #b5e08e; border-radius: 12px; }")
        frame_layout = QVBoxLayout(self.loading_frame)
        frame_layout.setContentsMargins(24, 14, 24, 14)
        self.loading_label = QLabel("Loading openings…")
        self.loading_label.setObjectName("AccentText")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self.loading_label)
        overlay_layout.addWidget(self.loading_frame, 0, Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addStretch(1)
        shadow = QGraphicsDropShadowEffect(self.loading_frame)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(181, 224, 142, 190))
        shadow.setOffset(0, 0)
        self.loading_frame.setGraphicsEffect(shadow)
        self.loading_animation = QPropertyAnimation(shadow, b"blurRadius", self)
        self.loading_animation.setDuration(900)
        self.loading_animation.setStartValue(8.0)
        self.loading_animation.setKeyValueAt(.5, 26.0)
        self.loading_animation.setEndValue(8.0)
        self.loading_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.loading_animation.setLoopCount(-1)
        self.loading_overlay.hide()

    def set_openings(self, openings):
        self.openings = list(openings or [])
        self.lines = [opening for opening in self.openings if opening.sans]
        self._grouped_lines = defaultdict(list)
        for opening in self.lines:
            self._grouped_lines[(opening.name, opening.eco.upper())].append(opening)
        self._catalog_ready = False
        self.refresh()

    def refresh(self, *_):
        self.begin_loading("Loading opening performance…")
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh_content)

    def _refresh_content(self):
        self._refresh_pending = False
        self._refresh_generation += 1
        generation = self._refresh_generation
        self.lines = self._for_side(self.side.currentData())
        if not self._catalog_ready:
            current = self.opening_choice.currentData()
            self.opening_choice.blockSignals(True)
            self.opening_choice.clear()
            self._choice_population = (generation, sorted(self._grouped_lines, key=lambda x: (x[0].casefold(), x[1])), 0, current)
            QTimer.singleShot(0, lambda: self._populate_opening_choices(generation))
            return
        self._finish_catalog_refresh(generation)

    def _populate_opening_choices(self, generation):
        population = self._choice_population
        if generation != self._refresh_generation or not population or population[0] != generation:
            return
        _token, keys, offset, current = population
        end = min(offset + 180, len(keys))
        for key in keys[offset:end]:
            self.opening_choice.addItem(f"{key[0]} · {key[1]}", key)
        if end < len(keys):
            self._choice_population = (generation, keys, end, current)
            QTimer.singleShot(0, lambda: self._populate_opening_choices(generation))
            return
        index = self.opening_choice.findData(current)
        if index >= 0:
            self.opening_choice.setCurrentIndex(index)
        self.opening_choice.blockSignals(False)
        self._catalog_ready = True
        self._choice_population = None
        self._finish_catalog_refresh(generation)

    def _finish_catalog_refresh(self, generation):
        if generation != self._refresh_generation:
            return
        self._select_opening()
        self._load_performance_records(generation)

    def _for_side(self, color):
        # Lines are shared; the selected color determines which plies the quiz asks.
        return self.lines

    def _load_performance_records(self, generation):
        if self._records_worker and self._records_worker.isRunning():
            self._records_reload_pending = True
            return
        worker = _OpeningPerformanceWorker(self.profile_store, generation, self)
        self._records_worker = worker
        worker.loaded.connect(self._performance_records_loaded)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._performance_worker_finished)
        worker.start()

    def _performance_worker_finished(self):
        self._records_worker = None
        if self._records_reload_pending:
            self._records_reload_pending = False
            self._load_performance_records(self._refresh_generation)

    def _performance_records_loaded(self, generation, records):
        if generation != self._refresh_generation:
            return
        self._fill_performance(records)
        self._select_opening()
        self.end_loading()

    def _fill_performance(self, records):
        self.stats = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0})
        self.stats_by_eco = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0})
        selected_color = "White" if self.side.currentData() == chess.WHITE else "Black"
        for game in records:
            if game.get("user_color") != selected_color:
                continue
            key = str(game.get("opening", "Unknown opening"))
            try:
                score = float(game.get("user_result"))
            except (TypeError, ValueError):
                score = None
            stat = self.stats[key]
            code_match = re.search(r"(?:·|\|)\s*([A-E]\d{2})\s*$", key, re.IGNORECASE)
            if code_match is None and re.fullmatch(r"[A-E]\d{2}", key.strip(), re.IGNORECASE):
                code_match = re.match(r"([A-E]\d{2})", key.strip(), re.IGNORECASE)
            eco_stat = self.stats_by_eco[code_match.group(1).upper()] if code_match else None
            stat["games"] += 1
            if eco_stat is not None:
                eco_stat["games"] += 1
            if score == 1: stat["w"] += 1
            elif score == .5: stat["d"] += 1
            elif score == 0: stat["l"] += 1
            if eco_stat is not None:
                if score == 1: eco_stat["w"] += 1
                elif score == .5: eco_stat["d"] += 1
                elif score == 0: eco_stat["l"] += 1
        rows = sorted(self.stats.items(), key=lambda pair: (-pair[1]["games"], pair[0].casefold()))
        self.performance.setRowCount(len(rows))
        for r, (name, stat) in enumerate(rows):
            win_rate = 100 * stat["w"] / stat["games"]
            focus = "Often played" if stat["games"] >= 3 else ("Needs work" if win_rate < 40 else "")
            values = (name, str(stat["games"]), f"{stat['w']}–{stat['d']}–{stat['l']}", f"{win_rate:.0f}%", focus)
            for c, value in enumerate(values):
                self.performance.setItem(r, c, QTableWidgetItem(value))

    def _select_opening(self, *_):
        key = self.opening_choice.currentData()
        self.line_list.clearSelection()
        self.line_list.setCurrentCell(-1, -1)
        self.line_list.setRowCount(0)
        if not key:
            self.summary.setText("Load the opening database in Profile → Settings to study repertoire lines.")
            return
        candidates = self._grouped_lines.get(key, [])
        stat = self.stats_by_eco.get(key[1].upper(), self.stats.get(f"{key[0]} · {key[1]}", {}))
        color_name = "White" if self.side.currentData() == chess.WHITE else "Black"
        self.summary.setText(f"{key[0]} · {key[1]}\n{color_name}: {stat.get('games', 0)} saved Chess.com games · {stat.get('w', 0)} wins, {stat.get('d', 0)} draws, {stat.get('l', 0)} losses")
        self.line_list.setRowCount(len(candidates))
        for row, op in enumerate(candidates):
            self.line_list.setItem(row, 0, QTableWidgetItem(f"Line {row + 1}"))
            moves = " ".join(f"{i // 2 + 1}{'.' if i % 2 == 0 else '...'} {san}" for i, san in enumerate(op.sans))
            self.line_list.setItem(row, 1, QTableWidgetItem(moves))

    def _performance_selected(self):
        items = self.performance.selectedItems()
        if not items: return
        label = items[0].text()
        index = self._opening_index_for_history_label(label)
        if index >= 0: self.opening_choice.setCurrentIndex(index)

    def _opening_index_for_history_label(self, label):
        """Match imported history by ECO code first, then normalized opening name."""
        label = str(label)
        match = re.search(r"(?:·|\|)\s*([A-E]\d{2})\s*$", label, re.IGNORECASE)
        if match is None and re.fullmatch(r"[A-E]\d{2}", label.strip(), re.IGNORECASE):
            match = re.match(r"([A-E]\d{2})", label.strip(), re.IGNORECASE)
        if match:
            code = match.group(1).upper()
            for index in range(self.opening_choice.count()):
                key = self.opening_choice.itemData(index)
                if key and str(key[1]).upper() == code:
                    return index
        normalized = re.sub(r"[^a-z0-9]+", "", str(label).casefold())
        for index in range(self.opening_choice.count()):
            if re.sub(r"[^a-z0-9]+", "", self.opening_choice.itemText(index).casefold()) == normalized:
                return index
        return -1

    def start_priority(self, mode):
        if not self.stats:
            self.prompt.setText("Download Chess.com games in Profile, or switch sides if you have not saved games for this color.")
            return
        names = list(self.stats)
        if mode == "frequent":
            name = max(names, key=lambda n: self.stats[n]["games"])
        else:
            eligible = [n for n in names if self.stats[n]["games"] >= 2]
            name = min(eligible or names, key=lambda n: self.stats[n]["w"] / self.stats[n]["games"])
        index = self._opening_index_for_history_label(name)
        if index < 0:
            self.prompt.setText(f"{name} is in your game history but has no matching line in the loaded opening database.")
            return
        self.opening_choice.setCurrentIndex(index)
        self.start_selected()

    def begin_loading(self, message="Loading openings…"):
        self.loading_label.setText(str(message))
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        if self.loading_animation.state() != QPropertyAnimation.State.Running:
            self.loading_animation.start()

    def end_loading(self):
        self.loading_animation.stop()
        self.loading_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.setGeometry(self.rect())

    def start_selected(self):
        key = self.opening_choice.currentData()
        options = self._grouped_lines.get(key, []) if key else []
        if not options:
            self.prompt.setText("No lines are available. Load the Lichess opening database in Profile → Settings.")
            return
        self.practice_line = options[0]
        selected = self.line_list.currentRow()
        if 0 <= selected < len(options):
            self.practice_line = options[selected]
        self.practice_ply = 0
        self.practice_board = chess.Board()
        self.last_reply = ""
        self.practice_active = True
        self._next_practice_move()

    def _clear_answers(self):
        while self.answers.count():
            item = self.answers.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _next_practice_move(self):
        self._clear_answers()
        if not self.practice_active or self.practice_ply >= len(self.practice_line.sans):
            self.practice_active = False
            self.prompt.setText("Line complete. Choose another line or repeat this opening to build recall.")
            return
        color = chess.WHITE if self.practice_ply % 2 == 0 else chess.BLACK
        san = self.practice_line.sans[self.practice_ply]
        if color != self.side.currentData():
            self.practice_board.push_san(san)
            self.practice_ply += 1
            self.last_reply = san
            self._next_practice_move()
            return
        choices = {san}
        # Include legal alternatives from sibling lines, plus plausible legal moves as distractors.
        for op in self.lines:
            if op.name == self.practice_line.name and len(op.sans) > self.practice_ply:
                candidate = op.sans[self.practice_ply]
                try:
                    if self.practice_board.parse_san(candidate) in self.practice_board.legal_moves: choices.add(candidate)
                except ValueError: pass
        legal_sans = [self.practice_board.san(move) for move in self.practice_board.legal_moves]
        import random
        distractors = [move for move in legal_sans if move not in choices]
        random.shuffle(distractors)
        choices.update(distractors[:max(0, 4 - len(choices))])
        prefix = f"Opponent replied {self.last_reply}.  " if self.last_reply else ""
        self.prompt.setText(f"{prefix}Your move as {'White' if color else 'Black'} · {self.practice_board.fullmove_number}…")
        for i, choice in enumerate(sorted(choices)):
            button = QPushButton(choice)
            button.clicked.connect(lambda checked=False, answer=choice: self._answer(answer, san))
            self.answers.addWidget(button, i // 4, i % 4)

    def _answer(self, answer, correct):
        if answer != correct:
            self.prompt.setText(f"Not that move. The line calls for {correct}; try again.")
            return
        self.practice_board.push_san(correct)
        self.practice_ply += 1
        self.last_reply = ""
        self._next_practice_move()
