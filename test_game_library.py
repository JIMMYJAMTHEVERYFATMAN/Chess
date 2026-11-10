from contextlib import closing
import os
import tempfile
import unittest
from unittest.mock import patch
from offline_chess.chesscom_data import ProfileStore
from offline_chess.game_library import GameLibrary
from offline_chess.training import TrainingStore
import chess


class GameLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':self.temp.name,'XDG_DATA_HOME':self.temp.name})
        self.env.start()
        self.store=ProfileStore()
        with closing(self.store.connect()) as db, db:
            for i in range(205):
                db.execute('''INSERT INTO chesscom_games(game_id,username,played_at,result,user_color,user_result,
                    opponent,time_control,time_class,rated,rules,opening,pgn_path,reviewed,review_json)
                    VALUES(?,?,?,'1-0','White',1,?,'600','rapid',1,'chess','Bird Opening · A02','',?,?)''',
                    (str(i),'testplayer','2026-01-01', 'ÖPPONENT' if i%2 else 'Someone',i%2,'x'*10000))
        self.library=GameLibrary(self.store)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_pages_are_bounded_stable_and_do_not_transfer_reviews(self):
        first,total,page=self.library.page('TESTPLAYER')
        second,_,_=self.library.page('testplayer',page=1)
        last,_,page=self.library.page('testplayer',page=999)
        self.assertEqual((len(first),len(second),len(last),total,page),(100,100,5,205,2))
        self.assertFalse({r['game_id'] for r in first}&{r['game_id'] for r in second})
        self.assertNotIn('review_json',first[0])
        self.assertEqual(len(self.library.get(first[0]['game_id'])['review_json']),10000)

    def test_filters_unicode_and_literal_sql_characters(self):
        rows,total,_=self.library.page('testplayer',search='öpponent',reviewed=True,speed='RAPID')
        self.assertEqual(total,102)
        self.assertTrue(all(r['reviewed'] for r in rows))
        self.assertEqual(self.library.page('testplayer',search="%' OR 1=1 --")[1],0)
        self.assertEqual(self.library.page('testplayer',allowed_ids=['1','2','2'])[1],2)

    def test_legacy_eco_only_opening_remains_searchable_by_name(self):
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE chesscom_games SET opening='A02'")
        self.assertEqual(self.library.page('testplayer',search='Bird')[1],205)

    def test_clear_all_also_clears_training_positions(self):
        TrainingStore(self.store).add(chess.STARTING_FEN,['e2e4'],'Opening','Test')
        self.store.clear_all()
        self.assertEqual(TrainingStore(self.store).progress(),[])
