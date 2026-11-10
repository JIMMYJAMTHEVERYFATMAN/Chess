"""Daily recall practice, using saved engine answers without an engine search."""
import json

import chess
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox

from ..training import TrainingStore
from .board import ChessBoard


class TrainingPage(QWidget):
    def __init__(self, profile):
        super().__init__()
        self.store = TrainingStore(profile) if profile else None
        self.queue = []
        self.card = None
        self.selected = None
        self.answered = False
        self.failed = False
        layout = QVBoxLayout(self)
        self.summary = QLabel('Train today · review a game to build your personal practice queue.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.board = ChessBoard()
        self.board.square_clicked.connect(self.on_square)
        layout.addWidget(self.board, 1)
        self.status = QLabel('Your answers and progress stay on this computer.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.promotion = QComboBox()
        self.promotion.addItems(['Queen', 'Rook', 'Bishop', 'Knight'])
        row.addWidget(QLabel('Promote to'))
        row.addWidget(self.promotion)
        self.reveal = QPushButton('Show answer')
        self.reveal.clicked.connect(self.show_answer)
        row.addWidget(self.reveal)
        self.next = QPushButton('Next position')
        self.next.clicked.connect(self.next_card)
        row.addWidget(self.next)
        start = QPushButton('Train today · up to 5 positions')
        start.clicked.connect(self.start)
        row.addWidget(start)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        if not self.store:
            self.summary.setText('Training unavailable: profile storage could not be opened.')
            return
        progress = self.store.progress()
        detail = ' · '.join(f'{topic}: {correct or 0}/{attempts or 0} recalled ({count} {"position" if count == 1 else "positions"})'
                            for topic, count, attempts, correct in progress)
        self.summary.setText(f'Train today · {len(self.store.due())} ready (up to 5 per session).\n' +
                             (detail or 'Review a game or practice an opening to add positions.'))

    def start(self):
        self.queue = self.store.due() if self.store else []
        self.next_card()

    def next_card(self):
        self.card = self.queue.pop(0) if self.queue else None
        self.selected = None
        self.answered = self.failed = False
        self.reveal.setEnabled(bool(self.card))
        if not self.card:
            self.status.setText('Session complete. Missed positions return in 10 minutes; recalled positions return tomorrow or later.')
            self.refresh()
            return
        self.position = chess.Board(self.card['fen'])
        self.board.flipped = self.position.turn == chess.BLACK
        self.board.set_position(self.position)
        self.status.setText(f"{self.card['topic']} · {'White' if self.position.turn else 'Black'} to move. Find the saved best move.")

    def on_square(self, square):
        if not self.card or self.answered:
            return
        if self.selected is None:
            piece = self.position.piece_at(square)
            if piece and piece.color == self.position.turn:
                self.selected = square
                self.board.set_position(self.position, selected=square)
            return
        moves = [m for m in self.position.legal_moves if m.from_square == self.selected and m.to_square == square]
        self.selected = None
        if len(moves) > 1:
            promotion = [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT][self.promotion.currentIndex()]
            moves = [m for m in moves if m.promotion == promotion]
        self.board.set_position(self.position)
        if not moves:
            return
        if moves[0].uci() not in json.loads(self.card['answers']):
            if not self.failed:
                self.store.record(self.card['card_id'], False)
            self.failed = True
            self.status.setText('Try another move. This position will return in 10 minutes.')
            return
        if not self.failed:
            self.store.record(self.card['card_id'], True)
        self.answered = True
        self.board.set_position(self.position, best_move=moves[0])
        self.status.setText('Correct. Continue to the next position.')
        self.refresh()

    def show_answer(self):
        if not self.card or self.answered:
            return
        if not self.failed:
            self.store.record(self.card['card_id'], False)
        self.answered = True
        answer = chess.Move.from_uci(json.loads(self.card['answers'])[0])
        self.board.set_position(self.position, best_move=answer)
        self.status.setText(f'{self.position.san(answer)}. This position will return in 10 minutes.')
        self.refresh()
