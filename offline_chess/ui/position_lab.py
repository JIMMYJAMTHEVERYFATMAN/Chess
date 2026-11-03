"""Position editor and top-move comparison page."""
from __future__ import annotations

import chess
from PyQt6.QtCore import QMimeData, QPoint, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .board import ChessBoard, EvalBar
from .theme import GLYPHS


PIECE_TYPES = (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN, chess.KING)
CASTLING_RIGHTS = (
    ("White · O-O", chess.WHITE, chess.BB_H1),
    ("White · O-O-O", chess.WHITE, chess.BB_A1),
    ("Black · O-O", chess.BLACK, chess.BB_H8),
    ("Black · O-O-O", chess.BLACK, chess.BB_A8),
)


class _PieceButton(QToolButton):
    """A palette piece that can be clicked to select or dragged onto the board."""

    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self._press_pos = QPoint()
        self._drag_started = False
        self.setText(GLYPHS[symbol])
        self.setProperty("pieceButton", True)
        self.setToolTip(f"Drag {symbol.upper()} onto a square, or click then click a square")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self._drag_started = True
            drag = QDrag(self)
            data = QMimeData()
            data.setText(self.symbol)
            drag.setMimeData(data)
            drag.setPixmap(self.grab())
            drag.setHotSpot(event.position().toPoint())
            drag.exec(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_started:
            self._drag_started = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _PieceRack(QFrame):
    piece_selected = pyqtSignal(str)

    def __init__(self, color: chess.Color, parent=None):
        super().__init__(parent)
        self.setObjectName("PieceRack")
        self.buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(5)
        layout.addStretch(1)
        for piece_type in PIECE_TYPES:
            piece = chess.Piece(piece_type, color)
            button = _PieceButton(piece.symbol(), self)
            button.clicked.connect(lambda _checked=False, symbol=piece.symbol(): self.piece_selected.emit(symbol))
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch(1)


class _PositionEditorBoard(ChessBoard):
    """ChessBoard with unrestricted piece placement for position setup."""

    position_edited = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setToolTip("Drag a piece from either rack, drag pieces to move them, or right-click to clear a square")
        self.palette_piece = None
        self._press_square = None
        self._selected_square = None

    def _square_at(self, point):
        side = min(self.width(), self.height())
        if side <= 0:
            return None
        unit = side / 8
        ox = (self.width() - side) / 2
        oy = (self.height() - side) / 2
        col = int((point.x() - ox) // unit)
        row = int((point.y() - oy) // unit)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        return chess.square(7 - col if self.flipped else col,
                            row if self.flipped else 7 - row)

    def set_palette_piece(self, symbol):
        self.palette_piece = symbol
        self._selected_square = None

    def mousePressEvent(self, event):
        square = self._square_at(event.position())
        if square is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._remove_piece(square)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_square = square
            event.accept()

    def mouseReleaseEvent(self, event):
        destination = self._square_at(event.position())
        source = self._press_square
        self._press_square = None
        if destination is None or event.button() != Qt.MouseButton.LeftButton:
            return

        if self.palette_piece:
            self._place_piece(destination, self.palette_piece)
        elif source is not None and source != destination and self.board.piece_at(source):
            self._move_piece(source, destination)
        elif self._selected_square is not None and self._selected_square != destination:
            self._move_piece(self._selected_square, destination)
        elif self.board.piece_at(destination):
            self._selected_square = destination
        else:
            self._selected_square = None
        event.accept()

    def dragEnterEvent(self, event):
        symbol = event.mimeData().text()
        if len(symbol) == 1 and symbol in "KQRBNPkqrbnp":
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._square_at(event.position()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        symbol = event.mimeData().text()
        square = self._square_at(event.position())
        if square is None or len(symbol) != 1 or symbol not in "KQRBNPkqrbnp":
            event.ignore()
            return
        self._place_piece(square, symbol)
        event.acceptProposedAction()

    def _prepare_edit(self):
        board = self.board.copy(stack=False)
        board.castling_rights = 0
        board.ep_square = None
        board.clear_stack()
        self._selected_square = None
        return board

    def _place_piece(self, square, symbol):
        board = self._prepare_edit()
        board.set_piece_at(square, chess.Piece.from_symbol(symbol))
        self._show_edited_board(board)

    def _remove_piece(self, square):
        if self.board.piece_at(square) is None:
            return
        board = self._prepare_edit()
        board.remove_piece_at(square)
        self._show_edited_board(board)

    def _move_piece(self, source, destination):
        piece = self.board.piece_at(source)
        if piece is None:
            return
        board = self._prepare_edit()
        board.remove_piece_at(source)
        board.set_piece_at(destination, piece)
        self._show_edited_board(board)

    def _show_edited_board(self, board):
        self.set_position(board.copy(stack=False))
        self.position_edited.emit(board.copy(stack=False))


class PositionAnalysisPage(QWidget):
    """Set up a position and compare the strongest moves for the selected side."""

    analysis_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.position = chess.Board()
        self._request_token = 0
        self._analyzing = False
        self._engine_ready = False
        self._results = {"White": [], "Black": []}
        self._active_line = []
        self._line_ply = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(18)

        board_column = QVBoxLayout()
        board_column.setContentsMargins(0, 0, 0, 0)
        board_column.setSpacing(4)
        self.black_rack = _PieceRack(chess.BLACK, self)
        self.black_rack.piece_selected.connect(self.select_palette_piece)
        board_column.addWidget(self.black_rack)
        board_row = QHBoxLayout()
        board_row.setSpacing(8)
        self.eval_bar = EvalBar()
        board_row.addWidget(self.eval_bar)
        self.board_preview = _PositionEditorBoard()
        self.board_preview.position_edited.connect(self._accept_board_edit)
        board_row.addWidget(self.board_preview, 1)
        board_column.addLayout(board_row, 1)
        self.white_rack = _PieceRack(chess.WHITE, self)
        self.white_rack.piece_selected.connect(self.select_palette_piece)
        board_column.addWidget(self.white_rack)
        self.editor_hint = QLabel("Drag pieces to move · add pieces from the racks · right-click a square to clear it")
        self.editor_hint.setObjectName("Muted")
        self.editor_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        board_column.addWidget(self.editor_hint)
        layout.addLayout(board_column, 1)

        self.panel = QFrame()
        self.panel.setObjectName("PositionPanel")
        self.panel.setMinimumWidth(360)
        self.panel.setMaximumWidth(510)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(11)
        layout.addWidget(self.panel)

        self._build_panel(panel_layout)
        self._sync_editor_state()

    def _build_panel(self, layout):
        top = QHBoxLayout()
        heading = QLabel("Set up the position")
        heading.setObjectName("PositionPanelTitle")
        top.addWidget(heading, 1)
        self.move_button = self._button("Move", "Button")
        self.move_button.setToolTip("Turn off piece placement to move pieces already on the board")
        self.move_button.clicked.connect(self.select_move_tool)
        top.addWidget(self.move_button)
        self.reset_button = self._button("Reset", "Button")
        self.reset_button.clicked.connect(self.reset_position)
        top.addWidget(self.reset_button)
        self.clear_button = self._button("Clear", "Button")
        self.clear_button.clicked.connect(self.clear_position)
        top.addWidget(self.clear_button)
        self.flip_button = self._button("Flip", "Button")
        self.flip_button.clicked.connect(self.flip_board)
        top.addWidget(self.flip_button)
        layout.addLayout(top)

        fen_card = QFrame()
        fen_card.setObjectName("PositionCard")
        fen_layout = QVBoxLayout(fen_card)
        fen_layout.setContentsMargins(10, 9, 10, 9)
        fen_layout.setSpacing(7)
        fen_label = QLabel("FEN")
        fen_label.setObjectName("Muted")
        fen_layout.addWidget(fen_label)
        fen_row = QHBoxLayout()
        self.fen_input = QLineEdit(self.position.fen())
        self.fen_input.setPlaceholderText("Paste a FEN or edit the board")
        self.fen_input.setClearButtonEnabled(True)
        self.fen_input.returnPressed.connect(self.load_fen)
        fen_row.addWidget(self.fen_input, 1)
        self.copy_fen_button = self._button("Copy", "Button")
        self.copy_fen_button.clicked.connect(self.copy_fen)
        fen_row.addWidget(self.copy_fen_button)
        fen_layout.addLayout(fen_row)
        self.load_fen_button = self._button("Load FEN", "Button")
        self.load_fen_button.clicked.connect(self.load_fen)
        fen_layout.addWidget(self.load_fen_button)
        layout.addWidget(fen_card)

        options = QHBoxLayout()
        options.setSpacing(12)
        turn_box = QVBoxLayout()
        turn_title = QLabel("TO MOVE")
        turn_title.setObjectName("Muted")
        turn_box.addWidget(turn_title)
        self.turn_group = QButtonGroup(self)
        turn_row = QHBoxLayout()
        self.white_to_move = QRadioButton("White")
        self.black_to_move = QRadioButton("Black")
        self.turn_group.addButton(self.white_to_move, chess.WHITE)
        self.turn_group.addButton(self.black_to_move, chess.BLACK)
        turn_row.addWidget(self.white_to_move)
        turn_row.addWidget(self.black_to_move)
        turn_box.addLayout(turn_row)
        self.turn_group.idToggled.connect(self._set_turn)
        options.addLayout(turn_box, 1)

        time_box = QVBoxLayout()
        time_title = QLabel("THINK FOR")
        time_title.setObjectName("Muted")
        time_box.addWidget(time_title)
        time_row = QHBoxLayout()
        self.time_group = QButtonGroup(self)
        self.time_choices = {}
        for seconds in (2, 4, 8):
            choice = QRadioButton(f"{seconds}s")
            self.time_group.addButton(choice, seconds)
            self.time_choices[seconds] = choice
            time_row.addWidget(choice)
        self.time_choices[8].setChecked(True)
        time_box.addLayout(time_row)
        options.addLayout(time_box, 1)
        layout.addLayout(options)

        self.castling_toggle = QToolButton()
        self.castling_toggle.setObjectName("CastlingToggle")
        self.castling_toggle.setText("Castling rights")
        self.castling_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.castling_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.castling_toggle.setCheckable(True)
        self.castling_toggle.toggled.connect(self._toggle_castling_panel)
        layout.addWidget(self.castling_toggle)
        self.castling_panel = QFrame()
        self.castling_panel.setObjectName("PositionCard")
        castling_grid = QGridLayout(self.castling_panel)
        castling_grid.setContentsMargins(8, 6, 8, 6)
        castling_grid.setSpacing(6)
        self.castling_checks = []
        for index, (label, color, square_bit) in enumerate(CASTLING_RIGHTS):
            check = QCheckBox(label)
            check.setToolTip(f"Preserve the {label} right in the FEN")
            check.toggled.connect(lambda _checked=False: self._apply_castling_controls())
            castling_grid.addWidget(check, index // 2, index % 2)
            self.castling_checks.append((check, color, square_bit))
        self.castling_panel.setVisible(False)
        layout.addWidget(self.castling_panel)

        answer_heading = QHBoxLayout()
        answer_title = QLabel("ENGINE'S ANSWER")
        answer_title.setObjectName("SectionTitle")
        answer_heading.addWidget(answer_title)
        answer_heading.addStretch()
        self.depth_label = QLabel("TOP 3 · WHITE TO MOVE")
        self.depth_label.setObjectName("Muted")
        answer_heading.addWidget(self.depth_label)
        layout.addLayout(answer_heading)

        self.white_table, self.white_panel = self._create_results_panel("WHITE TO MOVE")
        layout.addWidget(self.white_panel)
        self.black_table, self.black_panel = self._create_results_panel("BLACK TO MOVE")
        layout.addWidget(self.black_panel)

        self.status_label = QLabel("Waiting for Stockfish to start…")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        navigation = QHBoxLayout()
        self.back_button = self._button("← Back", "Button")
        self.back_button.setEnabled(False)
        self.back_button.clicked.connect(lambda: self.step_line(-1))
        navigation.addWidget(self.back_button)
        self.forward_button = self._button("Forward →", "Button")
        self.forward_button.setEnabled(False)
        self.forward_button.clicked.connect(lambda: self.step_line(1))
        navigation.addWidget(self.forward_button)
        self.analyze_button = self._button("Find the best move  →", "PrimaryButton")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.analyze_position)
        navigation.addWidget(self.analyze_button, 1)
        layout.addLayout(navigation)

    def _create_results_panel(self, title: str) -> tuple[QTableWidget, QWidget]:
        panel = QFrame()
        panel.setObjectName("PositionCard")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 7, 10, 8)
        panel_layout.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        panel_layout.addWidget(heading)
        table = QTableWidget(0, 4)
        table.setObjectName("PositionResults")
        table.setHorizontalHeaderLabels(["", "MOVE", "EVAL", "BEST LINE"])
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setMinimumHeight(104)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.cellClicked.connect(lambda row, _column, target=table: self._select_candidate(target, row))
        panel_layout.addWidget(table)
        return table, panel

    def _button(self, text, object_name):
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _table_for_color(self, color):
        return self.white_table if color == chess.WHITE else self.black_table

    def _color_for_table(self, table):
        return chess.WHITE if table is self.white_table else chess.BLACK

    def _toggle_castling_panel(self, expanded):
        self.castling_panel.setVisible(expanded)
        self.castling_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def select_palette_piece(self, symbol):
        self.board_preview.set_palette_piece(symbol)
        for button in self.black_rack.buttons + self.white_rack.buttons:
            button.setProperty("selectedPiece", button.symbol == symbol)
            button.style().unpolish(button)
            button.style().polish(button)
        self.editor_hint.setText(f"Selected {symbol.upper()} · click or drag it onto a square")

    def select_move_tool(self):
        self.board_preview.set_palette_piece(None)
        for button in self.black_rack.buttons + self.white_rack.buttons:
            button.setProperty("selectedPiece", False)
            button.style().unpolish(button)
            button.style().polish(button)
        self.editor_hint.setText("Drag pieces to move · add pieces from the racks · right-click a square to clear it")

    def _accept_board_edit(self, board):
        self.position = board.copy(stack=False)
        self._sync_editor_state()
        self.status_label.setText("Position edited. Find the best move when the setup is ready.")

    def _sync_editor_state(self):
        self.fen_input.setText(self.position.fen())
        self.board_preview.set_position(self.position.copy(stack=False))
        with QSignalBlocker(self.white_to_move):
            self.white_to_move.setChecked(self.position.turn == chess.WHITE)
        with QSignalBlocker(self.black_to_move):
            self.black_to_move.setChecked(self.position.turn == chess.BLACK)
        self._sync_castling_controls()
        self._update_selected_side()
        self._clear_results()
        self._reset_line_state()

    def _update_selected_side(self):
        color = self.position.turn
        label = "WHITE" if color == chess.WHITE else "BLACK"
        self.depth_label.setText(f"TOP 3 · {label} TO MOVE")
        self.white_panel.setVisible(color == chess.WHITE)
        self.black_panel.setVisible(color == chess.BLACK)

    def _sync_castling_controls(self):
        for checkbox, color, square_bit in self.castling_checks:
            with QSignalBlocker(checkbox):
                checkbox.setChecked(bool(self.position.castling_rights & square_bit))
            king_square = chess.E1 if color == chess.WHITE else chess.E8
            rook_square = (chess.H1 if square_bit == chess.BB_H1 else
                           chess.A1 if square_bit == chess.BB_A1 else
                           chess.H8 if square_bit == chess.BB_H8 else chess.A8)
            king = self.position.piece_at(king_square)
            rook = self.position.piece_at(rook_square)
            checkbox.setEnabled(bool(king and king.piece_type == chess.KING and king.color == color
                                     and rook and rook.piece_type == chess.ROOK and rook.color == color))

    def _apply_castling_controls(self):
        rights = 0
        for checkbox, _color, square_bit in self.castling_checks:
            if checkbox.isChecked():
                rights |= square_bit
        self.position.castling_rights = rights
        self.position.ep_square = None
        self._sync_editor_state()
        self.status_label.setText("Castling rights updated.")

    def _set_turn(self, color_id, checked):
        if not checked or self.position.turn == color_id:
            return
        self.position.turn = color_id
        self.position.ep_square = None
        self._sync_editor_state()
        self.status_label.setText(f"{('White' if color_id == chess.WHITE else 'Black')} to move.")

    def load_fen(self):
        try:
            board = chess.Board(self.fen_input.text().strip())
        except (ValueError, TypeError) as exc:
            self.status_label.setText(f"Could not read that FEN: {exc}")
            return False
        self.position = board
        self._sync_editor_state()
        if board.is_valid():
            self.status_label.setText("FEN loaded. You can adjust pieces or analyze the position.")
        else:
            self.status_label.setText("FEN loaded for editing. Complete or correct the position before analysis.")
        return True

    def copy_fen(self):
        self.fen_input.setText(self.position.fen())
        QApplication.clipboard().setText(self.position.fen())
        self.status_label.setText("FEN copied to clipboard.")

    def reset_position(self):
        self.position = chess.Board()
        self.board_preview.set_palette_piece(None)
        self._sync_editor_state()
        self.status_label.setText("Starting position restored.")

    def clear_position(self):
        self.position = chess.Board(None)
        self.board_preview.set_palette_piece(None)
        self._sync_editor_state()
        self.status_label.setText("Board cleared. Add pieces from the racks, then analyze.")

    def flip_board(self):
        self.board_preview.flipped = not self.board_preview.flipped
        self.board_preview.update()

    def set_engine_ready(self, ready: bool):
        self._engine_ready = bool(ready)
        self.analyze_button.setEnabled(self._engine_ready and not self._analyzing)
        if self._engine_ready and not self._analyzing:
            self.status_label.setText("Stockfish ready. Set up a position and find the best moves.")
        elif not self._engine_ready and not self._analyzing:
            self.status_label.setText("Waiting for Stockfish to start…")

    def set_engine_error(self, message: str):
        self._engine_ready = False
        self.analyze_button.setEnabled(False)
        self.status_label.setText(f"Stockfish is unavailable: {message}")

    def analyze_position(self):
        if not self._engine_ready or self._analyzing:
            return
        if not self.position.is_valid():
            self.status_label.setText("This position is not ready for analysis. Check that each side has one king and the FEN is legal.")
            return
        self._request_token += 1
        self._analyzing = True
        self.analyze_button.setEnabled(False)
        self.load_fen_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.white_to_move.setEnabled(False)
        self.black_to_move.setEnabled(False)
        seconds = self.time_group.checkedId()
        color = self.position.turn
        label = "White" if color == chess.WHITE else "Black"
        self.depth_label.setText(f"TOP 3 · {label.upper()} · UP TO {seconds}s")
        self.status_label.setText(f"Stockfish is finding the top three moves for {label}…")
        self._clear_results()
        self.analysis_requested.emit((self._request_token, self.position.copy(stack=False), seconds))

    def apply_results(self, payload):
        if not isinstance(payload, dict) or payload.get("token") != self._request_token:
            return
        self._analyzing = False
        self.load_fen_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self.white_to_move.setEnabled(True)
        self.black_to_move.setEnabled(True)
        self.analyze_button.setEnabled(self._engine_ready)
        if payload.get("fen") != self.position.fen():
            return
        error = payload.get("error")
        if error:
            self.status_label.setText(f"Position analysis failed: {error}")
            return

        self._results = payload.get("results", {"White": [], "Black": []})
        self._fill_table(self.white_table, self._results.get("White", []))
        self._fill_table(self.black_table, self._results.get("Black", []))
        active_color = payload.get("color", self.position.turn)
        selected_table = self._table_for_color(active_color)
        if selected_table.rowCount():
            selected_table.selectRow(0)
            self._select_candidate(selected_table, 0)
        label = "White" if active_color == chess.WHITE else "Black"
        self.status_label.setText(f"Top moves for {label} are ready.")

    def _fill_table(self, table: QTableWidget, rows):
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            rank = ("BEST", "2ND", "3RD")[index]
            values = [rank, row.get("san", "—"), row.get("eval", "—"), row.get("pv", "—")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column < 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, row.get("pv_uci", []))
                table.setItem(index, column, item)

    def _clear_results(self):
        self._results = {"White": [], "Black": []}
        for table in (self.white_table, self.black_table):
            table.setRowCount(0)
        self._reset_line_state()

    def _select_candidate(self, table, row_index):
        color = self._color_for_table(table)
        label = "White" if color == chess.WHITE else "Black"
        rows = self._results.get(label, [])
        if not 0 <= row_index < len(rows):
            return
        try:
            self._active_line = [chess.Move.from_uci(uci) for uci in rows[row_index].get("pv_uci", [])]
        except (ValueError, TypeError):
            self._active_line = []
        self._active_color = color
        self._line_ply = 0
        self._show_line_position()

    def _show_line_position(self):
        color = getattr(self, "_active_color", self.position.turn)
        board = self.position.copy(stack=False)
        board.turn = color
        last_move = None
        for move in self._active_line[:self._line_ply]:
            if move not in board.legal_moves:
                break
            board.push(move)
            last_move = move
        best_move = self._active_line[0] if self._line_ply == 0 and self._active_line else None
        self.board_preview.set_position(board, last_move, best_move=best_move)
        self.back_button.setEnabled(self._line_ply > 0)
        self.forward_button.setEnabled(self._line_ply < len(self._active_line))

    def step_line(self, step):
        if not self._active_line:
            return
        self._line_ply = max(0, min(len(self._active_line), self._line_ply + step))
        self._show_line_position()

    def _reset_line_state(self):
        self._active_line = []
        self._line_ply = 0
        self.back_button.setEnabled(False)
        self.forward_button.setEnabled(False)


__all__ = ["PositionAnalysisPage"]
