"""Custom and generated endgame challenges against Stockfish."""
from __future__ import annotations

import random

import chess
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QTabWidget, QFrame, QLineEdit, QMessageBox, QGridLayout,
)

from .board import ChessBoard
from .position_lab import _PieceRack, _PositionEditorBoard


class EndgameTrainerPage(QWidget):
    """Set up endgames or generate a material-advantage position to win."""

    move_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor_position = chess.Board(None)
        self.challenge_board = None
        self.challenge_start_board = None
        self.challenge_start_user_color = chess.WHITE
        self.user_color = chess.WHITE
        self.selected_square = None
        self.engine_thinking = False
        self.engine_ready = False
        self.challenge_active = False
        self.challenge_token = 0
        self.last_move = None
        self._build_ui()
        self._set_editor_position(self.random_position("Queen vs king", chess.WHITE))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        title = QLabel("Endgame Trainer")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        intro = QLabel("Set up an endgame or generate one, choose your side and engine strength, then try to checkmate.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("Play as"))
        self.side_choice = QComboBox()
        self.side_choice.addItem("White", chess.WHITE)
        self.side_choice.addItem("Black", chess.BLACK)
        settings.addWidget(self.side_choice)
        settings.addSpacing(12)
        settings.addWidget(QLabel("Engine strength"))
        self.difficulty = QSlider(Qt.Orientation.Horizontal)
        self.difficulty.setRange(400, 3190)
        self.difficulty.setSingleStep(10)
        self.difficulty.setPageStep(300)
        self.difficulty.setValue(1000)
        self.difficulty.setMinimumWidth(180)
        self.difficulty.valueChanged.connect(self._update_difficulty_label)
        settings.addWidget(self.difficulty, 1)
        self.difficulty_label = QLabel("~1000 Elo")
        self.difficulty_label.setMinimumWidth(90)
        settings.addWidget(self.difficulty_label)
        root.addLayout(settings)

        self.views = QTabWidget()
        self.setup_page = self._build_setup_page()
        self.challenge_page = self._build_challenge_page()
        self.views.addTab(self.setup_page, "Set up position")
        self.views.addTab(self.challenge_page, "Challenge")
        root.addWidget(self.views, 1)

    def _build_setup_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(18)
        board_col = QVBoxLayout()
        self.black_rack = _PieceRack(chess.BLACK, self)
        self.black_rack.piece_selected.connect(self._select_palette)
        board_col.addWidget(self.black_rack)
        self.editor_board = _PositionEditorBoard()
        self.editor_board.setMinimumSize(430, 430)
        self.editor_board.position_edited.connect(self._edited)
        board_col.addWidget(self.editor_board, 1)
        self.white_rack = _PieceRack(chess.WHITE, self)
        self.white_rack.piece_selected.connect(self._select_palette)
        board_col.addWidget(self.white_rack)
        hint = QLabel("Drag a piece to move it · select a rack piece to place it · right-click to clear a square")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        board_col.addWidget(hint)
        layout.addLayout(board_col, 1)

        panel = QFrame()
        panel.setObjectName("PositionPanel")
        panel.setMinimumWidth(300)
        controls = QVBoxLayout(panel)
        controls.setContentsMargins(16, 14, 16, 14)
        controls.setSpacing(10)
        heading = QLabel("Build an endgame")
        heading.setObjectName("PositionPanelTitle")
        controls.addWidget(heading)
        toolbar = QHBoxLayout()
        move_btn = QPushButton("Move pieces")
        move_btn.clicked.connect(lambda: self._select_palette(None))
        self.flip_btn = QPushButton("Flip board")
        self.flip_btn.clicked.connect(self._flip_editor)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_position)
        toolbar.addWidget(move_btn)
        toolbar.addWidget(self.flip_btn)
        toolbar.addWidget(clear_btn)
        controls.addLayout(toolbar)
        controls.addWidget(QLabel("Side to move"))
        self.turn_choice = QComboBox()
        self.turn_choice.addItem("White", chess.WHITE)
        self.turn_choice.addItem("Black", chess.BLACK)
        self.turn_choice.currentIndexChanged.connect(self._turn_changed)
        controls.addWidget(self.turn_choice)
        controls.addWidget(QLabel("FEN"))
        fen_row = QHBoxLayout()
        self.fen_input = QLineEdit()
        self.fen_input.setPlaceholderText("Paste a position FEN")
        self.fen_input.returnPressed.connect(self.load_fen)
        fen_row.addWidget(self.fen_input, 1)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self.load_fen)
        fen_row.addWidget(load_btn)
        controls.addLayout(fen_row)
        controls.addWidget(QLabel("Generated position"))
        self.position_type = QComboBox()
        self.position_type.addItems(["Queen vs king", "Rook vs king", "Pawn ending"])
        controls.addWidget(self.position_type)
        random_btn = QPushButton("Generate random endgame")
        random_btn.clicked.connect(self.generate_random)
        controls.addWidget(random_btn)
        self.setup_status = QLabel("Place pieces, load a FEN, or generate a position.")
        self.setup_status.setObjectName("Muted")
        self.setup_status.setWordWrap(True)
        controls.addWidget(self.setup_status)
        controls.addStretch(1)
        self.start_custom_btn = QPushButton("Start this endgame  →")
        self.start_custom_btn.setObjectName("PrimaryButton")
        self.start_custom_btn.clicked.connect(self.start_custom)
        controls.addWidget(self.start_custom_btn)
        layout.addWidget(panel)
        return page

    def _build_challenge_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(18)
        self.play_board = ChessBoard()
        self.play_board.setMinimumSize(430, 430)
        self.play_board.square_clicked.connect(self._square_clicked)
        layout.addWidget(self.play_board, 1)
        panel = QFrame()
        panel.setObjectName("PositionPanel")
        panel.setMinimumWidth(300)
        column = QVBoxLayout(panel)
        column.setContentsMargins(18, 16, 18, 16)
        column.setSpacing(12)
        heading = QLabel("Your endgame")
        heading.setObjectName("PositionPanelTitle")
        column.addWidget(heading)
        self.challenge_title = QLabel("Start a position to begin.")
        self.challenge_title.setObjectName("ReviewSummary")
        self.challenge_title.setWordWrap(True)
        column.addWidget(self.challenge_title)
        self.material_label = QLabel("")
        self.material_label.setObjectName("Muted")
        self.material_label.setWordWrap(True)
        column.addWidget(self.material_label)
        self.challenge_status = QLabel("You are trying to checkmate the engine.")
        self.challenge_status.setObjectName("Muted")
        self.challenge_status.setWordWrap(True)
        column.addWidget(self.challenge_status)
        column.addStretch(1)
        row = QHBoxLayout()
        retry = QPushButton("Restart")
        retry.clicked.connect(self.restart_challenge)
        setup = QPushButton("Edit position")
        setup.clicked.connect(lambda: self.views.setCurrentWidget(self.setup_page))
        row.addWidget(retry)
        row.addWidget(setup)
        column.addLayout(row)
        layout.addWidget(panel)
        return page

    def _update_difficulty_label(self, value):
        self.difficulty_label.setText(f"~{value} Elo" if value < 1320 else f"{value} Elo")
        if self.challenge_board is not None:
            self.material_label.setText(f"{len(self.challenge_board.piece_map())} pieces · {self.difficulty_label.text()} engine")

    def _flip_editor(self):
        self.editor_board.flipped = not self.editor_board.flipped
        self.editor_board.update()

    def set_engine_ready(self, ready, message=""):
        self.engine_ready = bool(ready)
        if not self.engine_ready and message:
            self._set_status(f"Stockfish is unavailable: {message}")
            self.engine_thinking = False
            self.challenge_active = False
        elif self.engine_ready and self.challenge_active and self.challenge_board.turn != self.user_color:
            self._request_engine_move()

    def _select_palette(self, symbol):
        self.editor_board.set_palette_piece(symbol)
        for button in self.black_rack.buttons + self.white_rack.buttons:
            button.setProperty("selectedPiece", button.symbol == symbol)
            button.style().unpolish(button)
            button.style().polish(button)
        if symbol:
            self.setup_status.setText(f"Selected {symbol.upper()} · click a square to place it, or drag it onto the board.")
        else:
            self.setup_status.setText("Move pieces on the board or select a piece from either rack.")

    def _set_editor_position(self, board):
        self.editor_position = board.copy(stack=False)
        self.editor_board.set_position(self.editor_position.copy(stack=False))
        self.editor_board.set_palette_piece(None)
        self._sync_editor()

    def _edited(self, board):
        self.editor_position = board.copy(stack=False)
        self._sync_editor()

    def _sync_editor(self):
        self.fen_input.setText(self.editor_position.fen())
        self.turn_choice.blockSignals(True)
        self.turn_choice.setCurrentIndex(0 if self.editor_position.turn == chess.WHITE else 1)
        self.turn_choice.blockSignals(False)
        valid, message = self._validate_endgame(self.editor_position)
        self.start_custom_btn.setEnabled(valid)
        self.setup_status.setText("Position is ready to play." if valid else message)

    def _turn_changed(self, index):
        self.editor_position.turn = self.turn_choice.itemData(index)
        self.editor_position.ep_square = None
        self.editor_board.set_position(self.editor_position.copy(stack=False))
        self._sync_editor()

    def clear_position(self):
        self._set_editor_position(chess.Board(None))

    def load_fen(self):
        try:
            board = chess.Board(self.fen_input.text().strip())
        except (ValueError, TypeError) as exc:
            self.setup_status.setText(f"Could not read that FEN: {exc}")
            return
        self._set_editor_position(board)

    @staticmethod
    def _validate_endgame(board):
        if board.king(chess.WHITE) is None or board.king(chess.BLACK) is None:
            return False, "Add one king for each side."
        if len(board.piece_map()) > 16:
            return False, "Endgame practice positions are limited to sixteen pieces."
        if sum(piece.piece_type != chess.KING for piece in board.piece_map().values()) < 1:
            return False, "Add at least one piece besides the kings."
        if not board.is_valid():
            return False, "This position is not legal. Check king placement and which side is to move."
        if board.is_game_over():
            return False, "This position is already over. Adjust the pieces or side to move."
        if not any(board.legal_moves):
            return False, "This position has no legal moves."
        return True, ""

    def generate_random(self):
        board = self.random_position(self.position_type.currentText(), self.side_choice.currentData())
        self._set_editor_position(board)
        self.setup_status.setText("Random endgame generated. Adjust it if you like, then start the challenge.")

    @staticmethod
    def random_position(kind, user_color):
        rng = random.SystemRandom()
        for _ in range(1000):
            board = chess.Board(None)
            squares = rng.sample(range(64), 5 if kind == "Pawn ending" else 3)
            board.set_piece_at(squares[0], chess.Piece(chess.KING, user_color))
            board.set_piece_at(squares[1], chess.Piece(chess.KING, not user_color))
            if kind == "Queen vs king":
                board.set_piece_at(squares[2], chess.Piece(chess.QUEEN, user_color))
            elif kind == "Rook vs king":
                board.set_piece_at(squares[2], chess.Piece(chess.ROOK, user_color))
            else:
                pawn_squares = [sq for sq in squares[2:] if chess.square_rank(sq) in range(1, 7)]
                if len(pawn_squares) < 3:
                    continue
                board.set_piece_at(pawn_squares[0], chess.Piece(chess.PAWN, user_color))
                board.set_piece_at(pawn_squares[1], chess.Piece(chess.PAWN, user_color))
                board.set_piece_at(pawn_squares[2], chess.Piece(chess.PAWN, not user_color))
            board.turn = user_color
            if board.is_valid() and not board.is_game_over() and any(board.legal_moves):
                return board
        # Safe fallback, also useful if a rare random layout is rejected repeatedly.
        board = chess.Board(None)
        board.set_piece_at(chess.E1, chess.Piece(chess.KING, user_color))
        board.set_piece_at(chess.E8, chess.Piece(chess.KING, not user_color))
        board.set_piece_at(chess.A1 if user_color == chess.WHITE else chess.A8,
                           chess.Piece(chess.QUEEN, user_color))
        board.turn = user_color
        return board

    def start_custom(self):
        valid, message = self._validate_endgame(self.editor_position)
        if not valid:
            self.setup_status.setText(message)
            return
        self._start_challenge(self.editor_position.copy(stack=False), self.side_choice.currentData())

    def start_from_game_position(self, board, user_color):
        """Load a position from a reviewed game's endgame and begin a challenge."""
        valid, message = self._validate_endgame(board)
        if not valid:
            return False, message
        self._set_editor_position(board)
        self.side_choice.setCurrentIndex(0 if user_color == chess.WHITE else 1)
        self._start_challenge(board.copy(stack=False), user_color)
        self.challenge_title.setText(f"Game endgame · Play as {'White' if user_color else 'Black'}")
        return True, ""

    def _start_challenge(self, board, user_color):
        self.challenge_token += 1
        self.challenge_board = board.copy(stack=False)
        self.challenge_start_board = board.copy(stack=False)
        self.challenge_start_user_color = user_color
        self.user_color = user_color
        self.selected_square = None
        self.last_move = None
        self.challenge_active = True
        self.engine_thinking = False
        self.play_board.flipped = user_color == chess.BLACK
        self.play_board.set_position(self.challenge_board.copy(stack=False))
        pieces = [piece.piece_type for piece in board.piece_map().values()]
        title = ("Queen endgame" if chess.QUEEN in pieces else
                 "Rook endgame" if chess.ROOK in pieces else
                 "Minor-piece endgame" if any(kind in pieces for kind in (chess.BISHOP, chess.KNIGHT)) else
                 "Pawn endgame")
        self.challenge_title.setText(f"{title} · Play as {'White' if user_color else 'Black'}")
        self.material_label.setText(f"{len(board.piece_map())} pieces · {self.difficulty_label.text()} engine")
        self.views.setCurrentWidget(self.challenge_page)
        self._set_status("Your move." if board.turn == user_color else "Stockfish is thinking…")
        if board.turn != user_color:
            self._request_engine_move()

    def _set_status(self, message):
        self.challenge_status.setText(message)

    def _square_clicked(self, square):
        if not self.challenge_active or self.engine_thinking or self.challenge_board.turn != self.user_color:
            return
        piece = self.challenge_board.piece_at(square)
        if self.selected_square is None:
            if piece and piece.color == self.user_color:
                self.selected_square = square
                self.play_board.set_position(self.challenge_board.copy(stack=False), self.last_move, selected=square)
            return
        candidates = [move for move in self.challenge_board.legal_moves
                      if move.from_square == self.selected_square and move.to_square == square]
        if candidates:
            move = next((item for item in candidates if item.promotion == chess.QUEEN), candidates[0])
            self.selected_square = None
            self._push_move(move)
        elif piece and piece.color == self.user_color:
            self.selected_square = square
            self.play_board.set_position(self.challenge_board.copy(stack=False), self.last_move, selected=square)
        else:
            self.selected_square = None
            self.play_board.set_position(self.challenge_board.copy(stack=False), self.last_move)

    def _push_move(self, move):
        self.last_move = move
        self.challenge_board.push(move)
        self.play_board.set_position(self.challenge_board.copy(stack=False), move)
        self._check_outcome()
        if self.challenge_active and self.challenge_board.turn != self.user_color:
            self._request_engine_move()
        elif self.challenge_active:
            self._set_status("Your move.")

    def _request_engine_move(self):
        if not self.challenge_active or self.challenge_board.is_game_over():
            self._check_outcome()
            return
        if not self.engine_ready:
            self._set_status("Waiting for Stockfish to start…")
            return
        self.engine_thinking = True
        self._set_status("Stockfish is thinking…")
        self.move_requested.emit((self.challenge_token, self.challenge_board.copy(stack=False), self.difficulty.value()))

    def apply_engine_move(self, result):
        if not isinstance(result, dict) or result.get("token") != self.challenge_token or not self.challenge_active:
            return
        if result.get("fen") != self.challenge_board.fen():
            return
        self.engine_thinking = False
        if result.get("error"):
            self.challenge_active = False
            self._set_status(f"Engine move failed: {result['error']}")
            return
        try:
            move = chess.Move.from_uci(result.get("move", ""))
            if move not in self.challenge_board.legal_moves:
                raise ValueError("Stockfish returned an illegal move")
        except (ValueError, TypeError) as exc:
            self.challenge_active = False
            self._set_status(f"Could not apply the engine move: {exc}")
            return
        self._push_move(move)

    def _check_outcome(self):
        board = self.challenge_board
        if board.is_checkmate():
            winner = not board.turn
            self.challenge_active = False
            self.engine_thinking = False
            if winner == self.user_color:
                self._set_status("You checkmated Stockfish. Challenge won!")
            else:
                self._set_status("Stockfish checkmated you. Challenge lost.")
                self._show_failure_popup()
        elif board.is_stalemate() or board.is_insufficient_material() or board.can_claim_draw():
            self.challenge_active = False
            self.engine_thinking = False
            self._set_status("Draw. Restart the position and try again.")

    def restart_challenge(self):
        if self.challenge_start_board is None:
            self.views.setCurrentWidget(self.setup_page)
            return
        self._start_challenge(self.challenge_start_board.copy(stack=False), self.challenge_start_user_color)

    def _show_failure_popup(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Endgame challenge failed")
        dialog.setText("Checkmate. Stockfish wins.")
        dialog.setInformativeText("Would you like to restart this endgame position?")
        restart = dialog.addButton("Restart", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(restart)
        dialog.exec()
        if dialog.clickedButton() is restart:
            self.restart_challenge()


__all__ = ["EndgameTrainerPage"]
