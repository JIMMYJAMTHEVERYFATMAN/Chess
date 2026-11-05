"""Opening repertoire study and Chess.com performance practice."""
from collections import defaultdict

import chess
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QGridLayout,
)


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

    def set_openings(self, openings):
        self.openings = list(openings or [])
        self.refresh()

    def refresh(self, *_):
        self.lines = self._for_side(self.side.currentData())
        current = self.opening_choice.currentData()
        grouped = {}
        for opening in self.lines:
            grouped.setdefault((opening.name, opening.eco), []).append(opening)
        self.opening_choice.blockSignals(True)
        self.opening_choice.clear()
        for key in sorted(grouped, key=lambda x: (x[0].casefold(), x[1])):
            self.opening_choice.addItem(f"{key[0]} · {key[1]}", key)
        index = self.opening_choice.findData(current)
        if index >= 0:
            self.opening_choice.setCurrentIndex(index)
        self.opening_choice.blockSignals(False)
        self._fill_performance()
        self._select_opening()

    def _for_side(self, color):
        # Lines are shared; the selected color determines which plies the quiz asks.
        return [op for op in self.openings if op.sans]

    def _records(self):
        if not self.profile_store:
            return []
        try:
            username = self.profile_store.get_setting("chesscom_username", "")
            return self.profile_store.chesscom_games(username) if username else []
        except Exception:
            return []

    def _fill_performance(self):
        self.stats = defaultdict(lambda: {"games": 0, "w": 0, "d": 0, "l": 0})
        selected_color = "White" if self.side.currentData() == chess.WHITE else "Black"
        for game in self._records():
            if game.get("user_color") != selected_color:
                continue
            key = str(game.get("opening", "Unknown opening"))
            try:
                score = float(game.get("user_result"))
            except (TypeError, ValueError):
                score = None
            stat = self.stats[key]
            stat["games"] += 1
            if score == 1: stat["w"] += 1
            elif score == .5: stat["d"] += 1
            elif score == 0: stat["l"] += 1
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
        candidates = [op for op in self.lines if (op.name, op.eco) == key]
        stat = self.stats.get(f"{key[0]} · {key[1]}", {})
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
        index = self.opening_choice.findText(label)
        if index >= 0: self.opening_choice.setCurrentIndex(index)

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
        index = self.opening_choice.findText(name)
        if index < 0:
            self.prompt.setText(f"{name} is in your game history but has no matching line in the loaded opening database.")
            return
        self.opening_choice.setCurrentIndex(index)
        self.start_selected()

    def start_selected(self):
        key = self.opening_choice.currentData()
        options = [op for op in self.lines if key and (op.name, op.eco) == key]
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
