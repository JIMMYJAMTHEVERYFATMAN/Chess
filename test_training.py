import json
import os
import tempfile
import unittest
from unittest.mock import patch

import chess
from PyQt6.QtWidgets import QApplication
from offline_chess.chesscom_data import ProfileStore
from offline_chess.training import TrainingStore
from offline_chess.ui.training_page import TrainingPage
from offline_chess.ui.opening_trainer import OpeningTrainerPage
from offline_chess.openings import Opening
from types import SimpleNamespace


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name, 'XDG_DATA_HOME': self.temp.name})
        self.env.start()
        self.profile = ProfileStore()
        self.store = TrainingStore(self.profile)
        self.key = self.store.add(chess.STARTING_FEN, ['e2e4'], 'Opening recall', 'Test')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_deduplication_and_persistent_schedule(self):
        self.assertEqual(self.key, self.store.add(chess.STARTING_FEN, ['e2e4'], 'Opening recall', 'Another game'))
        self.store.record(self.key, True, now=1000)
        self.assertEqual(self.store.due(now=1001), [])
        self.assertEqual(len(TrainingStore(self.profile).due(now=87400)), 1)
        self.store.record(self.key, False, now=90000)
        self.assertEqual(len(self.store.due(now=90600)), 1)
        self.assertEqual(self.store.progress()[0][2:], (2, 1))

    def test_illegal_answers_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add(chess.STARTING_FEN, ['e2e5'], 'x', 'x')

    def test_wrong_then_correct_does_not_advance_schedule(self):
        page = TrainingPage(self.profile)
        page.start()
        for square in (chess.D2, chess.D4, chess.E2, chess.E4):
            page.on_square(square)
        self.assertTrue(page.answered)
        row = self.store.progress()[0]
        self.assertEqual(row[2:], (1, 0))
        self.assertEqual(self.store.due(), [])
        page.close()

    def test_black_review_uses_position_before_the_mistake(self):
        board=chess.Board()
        history=[chess.Move.from_uci('e2e4'),chess.Move.from_uci('a7a6')]
        item=SimpleNamespace(ply=2,verdict='Mistake',best_move=chess.Move.from_uci('e7e5'),
                             acceptable_moves=(),theme='Development',phase='Opening')
        self.store.add_review(board,history,[item],chess.BLACK)
        cards=self.store.due()
        black=next(card for card in cards if card['topic']=='Development')
        position=chess.Board(black['fen'])
        self.assertFalse(position.turn)
        self.assertEqual(position.piece_at(chess.E4),chess.Piece(chess.PAWN,chess.WHITE))
        self.assertEqual(json.loads(black['answers']),['e7e5'])

    def test_opening_drill_advances_board_and_saves_recall(self):
        page=OpeningTrainerPage(self.profile)
        page._grouped_lines={('Test','A00'):[Opening('A00','Test',('e4','e5','Nf3'),())]}
        page.opening_choice.addItem('Test',('Test','A00'))
        page.start_selected()
        page._board_answer(chess.E2)
        page._board_answer(chess.E4)
        self.assertEqual(page.practice_ply,2)
        self.assertEqual(page.practice_board.piece_at(chess.E5),chess.Piece(chess.PAWN,chess.BLACK))
        self.assertEqual(self.store.progress()[0][2:],(1,1))
        page.close()

    def test_reveal_counts_as_failed_recall(self):
        page = TrainingPage(self.profile)
        page.start()
        page.show_answer()
        page.show_answer()
        self.assertEqual(self.store.progress()[0][2:], (1, 0))
        page.close()
