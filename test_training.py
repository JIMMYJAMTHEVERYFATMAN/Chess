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

    def test_reveal_counts_as_failed_recall(self):
        page = TrainingPage(self.profile)
        page.start()
        page.show_answer()
        page.show_answer()
        self.assertEqual(self.store.progress()[0][2:], (1, 0))
        page.close()
