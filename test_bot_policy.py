import random
import unittest
import chess
import chess.engine
from offline_chess.bot_policy import choose_candidate, adaptive_target
from tools.bot_benchmark import interval


class BotPolicyTests(unittest.TestCase):
    def test_adaptive_strength_is_bounded_and_draws_preserve_target(self):
        self.assertEqual(adaptive_target(800,1),850)
        self.assertEqual(adaptive_target(800,0),750)
        self.assertEqual(adaptive_target(800,.5),800)
        self.assertEqual(adaptive_target(400,0),400)
        self.assertEqual(adaptive_target(3190,1),3190)
        with self.assertRaises(ValueError):adaptive_target(800,2)

    def test_styles_are_legal_and_seeded(self):
        board=chess.Board()
        infos=[{'pv':[chess.Move.from_uci(move)],'score':chess.engine.PovScore(chess.engine.Cp(cp),chess.WHITE)}
               for move,cp in [('e2e4',20),('d2d4',10),('a2a3',-200)]]
        for style in ('balanced','attacking','solid'):
            a=choose_candidate(board,infos,800,style,random.Random(13))
            b=choose_candidate(board,infos,800,style,random.Random(13))
            self.assertEqual(a,b)
            self.assertIn(a,board.legal_moves)
        rng=random.Random(12)
        solid=sum(choose_candidate(board,infos,400,'solid',rng).uci()=='a2a3' for _ in range(1000))
        rng=random.Random(12)
        balanced=sum(choose_candidate(board,infos,400,'balanced',rng).uci()=='a2a3' for _ in range(1000))
        self.assertLess(solid,balanced)

    def test_uncertainty_with_draws_and_small_samples(self):
        self.assertIsNone(interval([]))
        self.assertEqual(interval([.5]),[0,1])
        low,high=interval([.5]*100)
        self.assertLess(low,.5)
        self.assertGreater(high,.5)
