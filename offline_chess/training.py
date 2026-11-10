"""Persistent, local spaced repetition shared by review and opening practice."""
import hashlib
import json
import time

import chess


class TrainingStore:
    def __init__(self, profile):
        self.profile = profile
        with profile.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS training_cards(
                    card_id TEXT PRIMARY KEY, fen TEXT NOT NULL, answers TEXT NOT NULL,
                    topic TEXT NOT NULL, source TEXT NOT NULL, due REAL NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    correct INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS training_due ON training_cards(due);
                CREATE TABLE IF NOT EXISTS training_attempts(
                    card_id TEXT NOT NULL, attempted_at REAL NOT NULL, correct INTEGER NOT NULL);
            ''')

    def add(self, fen, answers, topic, source):
        board = chess.Board(fen)
        answers = sorted({m.uci() if isinstance(m, chess.Move) else m for m in answers})
        if not answers or any(chess.Move.from_uci(m) not in board.legal_moves for m in answers):
            raise ValueError('Training answers must be legal moves')
        # Same position/answer is one card even if encountered in multiple games.
        key = hashlib.sha256((fen + json.dumps(answers)).encode()).hexdigest()
        with self.profile.connect() as db:
            db.execute('INSERT OR IGNORE INTO training_cards(card_id,fen,answers,topic,source) VALUES(?,?,?,?,?)',
                       (key, fen, json.dumps(answers), topic, source))
        return key

    def add_review(self, start_board, history, items, color):
        by_ply = {item.ply: item for item in items}
        board = start_board.copy(stack=False)
        added = 0
        for ply, move in enumerate(history, 1):
            item = by_ply.get(ply)
            if (item and board.turn == color and item.verdict in ('Miss', 'Inaccuracy', 'Mistake', 'Blunder')
                    and item.best_move in board.legal_moves):
                answers = [m for m in (item.best_move, *item.acceptable_moves) if m in board.legal_moves]
                self.add(board.fen(), answers, item.theme or item.phase or 'Review mistakes', 'Game review')
                added += 1
            if move not in board.legal_moves:
                break
            board.push(move)
        return added

    def due(self, limit=5, now=None):
        now = time.time() if now is None else now
        with self.profile.connect() as db:
            db.row_factory = __import__('sqlite3').Row
            return [dict(row) for row in db.execute(
                'SELECT * FROM training_cards WHERE due<=? ORDER BY due,card_id LIMIT ?', (now, limit))]

    def record(self, card_id, correct, now=None):
        now = time.time() if now is None else now
        with self.profile.connect() as db:
            row = db.execute('SELECT streak FROM training_cards WHERE card_id=?', (card_id,)).fetchone()
            if row is None:
                raise KeyError(card_id)
            streak = row[0] + 1 if correct else 0
            delay = min(60, 2 ** min(streak - 1, 6)) * 86400 if correct else 600
            db.execute('UPDATE training_cards SET due=?,streak=?,attempts=attempts+1,correct=correct+? WHERE card_id=?',
                       (now + delay, streak, int(correct), card_id))
            db.execute('INSERT INTO training_attempts VALUES(?,?,?)', (card_id, now, int(correct)))

    def progress(self):
        with self.profile.connect() as db:
            return db.execute('SELECT topic,COUNT(*),SUM(attempts),SUM(correct) FROM training_cards GROUP BY topic ORDER BY topic').fetchall()
