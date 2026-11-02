import unittest
import io
import json
from types import SimpleNamespace
import os
import sqlite3
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

try:
    import chess
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from main import (
        analyze_review_events,
        removed_defender_event,
        tactical_themes,
        classify,
        engine_line_forces_mate,
        brilliant_choice_gap,
        only_good_move_gap,
        generate_review_commentary,
        review_reflection_prompt,
        review_practice_hint,
        EngineWorker,
        game_phase,
        is_engine_justified_sacrifice,
        can_label_brilliant,
        normalized_move_loss,
        override_review_category,
        ReviewEvent,
        ReviewMove,
        serialize_review_moves,
        deserialize_review_moves,
        ProfileStore,
        ProfileDialog,
        ChessComFetchWorker,
        chesscom_request_json,
        normalize_chesscom_username,
        validate_chesscom_archive_url,
        prepare_chesscom_pgn,
        chesscom_opening_label,
        predicted_performance_elo,
        REVIEW_ANALYSIS_VERSION,
        game_coach_summary,
        personalized_review_coach,
        review_game_narrative,
        near_best_moves,
        player_review_overview,
        review_filter_indices,
        review_row_ply,
        review_practice_moves,
        recurring_review_themes,
        format_review_impact,
        review_focus_squares,
        review_spoken_text,
        rank_review_moments,
        review_moment_tags,
        QApplication,
        ChessWindow,
        BOARD_THEMES,
        PIECE_STYLES,
        QPushButton,
        QHeaderView,
        ReviewSummaryDialog,
        annotate_review_pgn,
        MistakePracticeDialog,
    )
    TEST_IMPORT_ERROR=None
except (ImportError,SystemExit) as exc:
    chess=None
    TEST_IMPORT_ERROR=str(exc)


def position(pieces, turn=None):
    board=chess.Board(None)
    for color,kind,square in pieces:
        board.set_piece_at(chess.parse_square(square),chess.Piece(kind,color))
    board.turn=chess.WHITE if turn is None else turn
    return board


def play(board, san):
    move=board.parse_san(san)
    board.push(move)
    return move


@unittest.skipIf(TEST_IMPORT_ERROR is not None,f"App review dependencies unavailable: {TEST_IMPORT_ERROR}")
class ReviewAnalysisTests(unittest.TestCase):
    def test_chesscom_eco_codes_display_readable_opening_names(self):
        self.assertEqual(chesscom_opening_label("A02"),"Bird Opening · A02")
        self.assertEqual(chesscom_opening_label("","C20","https://www.chess.com/openings/Kings-Pawn-Game"),
                         "King's Pawn Game · C20")
        self.assertEqual(chesscom_opening_label("Bird Opening: Lasker Variation","A03"),
                         "Bird Opening: Lasker Variation · A03")

    def test_chesscom_username_and_archive_urls_are_validated(self):
        self.assertEqual(normalize_chesscom_username("  Player_1 "),"Player_1")
        for invalid in ("", "x", "a/b", "bad name", "name?x=1"):
            with self.subTest(username=invalid),self.assertRaises(ValueError):
                normalize_chesscom_username(invalid)
        valid="https://api.chess.com/pub/player/Player_1/games/2026/10"
        self.assertEqual(validate_chesscom_archive_url(valid),valid)
        for invalid in ("http://api.chess.com/pub/player/x/games/2026/10",
                        "https://example.com/pub/player/x/games/2026/10",
                        "https://api.chess.com/pub/player/x/games/2026/99"):
            with self.subTest(url=invalid),self.assertRaises(ValueError):
                validate_chesscom_archive_url(invalid)

    def test_chesscom_archive_worker_fetches_all_months_serially(self):
        archives=["https://api.chess.com/pub/player/player1/games/2026/09",
                  "https://api.chess.com/pub/player/player1/games/2026/10"]
        with patch("main.chesscom_request_json",side_effect=[{"archives":archives},
                {"games":[{"id":"september"}]},{"games":[{"id":"october"}]}]) as request:
            worker=ChessComFetchWorker("all","player1")
            downloaded=[];finished=[]
            worker.month_downloaded.connect(lambda month,games:downloaded.append((month,games)))
            worker.all_finished.connect(lambda:finished.append(True))
            worker.run()
        self.assertEqual([call.args[0] for call in request.call_args_list],archives[:0]+[
            "https://api.chess.com/pub/player/player1/games/archives",*archives])
        self.assertEqual([month for month,_ in downloaded],archives)
        self.assertEqual(finished,[True])

    def test_chesscom_request_retries_a_transient_connection_reset(self):
        response=MagicMock()
        response.__enter__.return_value=response
        response.read.return_value=b'{"archives":[]}'
        response.headers.get.return_value=None
        reset=urllib.error.URLError(ConnectionResetError(10054,"connection forcibly closed"))
        with patch("main.urllib.request.urlopen",side_effect=[reset,response]) as urlopen, \
                patch("main.time.sleep") as sleep:
            data=chesscom_request_json("https://api.chess.com/pub/player/player1/games/archives")
        self.assertEqual(data,{"archives":[]})
        self.assertEqual(urlopen.call_count,2)
        sleep.assert_called_once_with(1)

    def test_chesscom_pgn_files_are_deduplicated_and_review_state_survives_redownload(self):
        previous=os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as data_dir:
            os.environ["LOCALAPPDATA"]=data_dir
            try:
                store=ProfileStore()
                pgn='[Event "Online"]\n[Site "Chess.com"]\n[Date "2026.10.31"]\n[White "player1"]\n[Black "opponent"]\n[Result "1-0"]\n[ECO "C20"]\n\n1. e4 e5 2. Qh5 Nc6 1-0\n'
                game={"url":"https://www.chess.com/game/live/12345","end_time":1793450000,
                      "rated":True,"time_control":"600+5","time_class":"rapid","rules":"chess",
                      "eco":"https://www.chess.com/openings/Kings-Pawn-Game","pgn":pgn,
                      "white":{"username":"player1","rating":1500,"result":"win"},
                      "black":{"username":"opponent","rating":1480,"result":"checkmated"}}
                game_id=store.save_chesscom_game("player1",game)
                record=store.chesscom_games("player1")[0]
                self.assertEqual(record["game_id"],game_id)
                self.assertEqual(record["user_color"],"White")
                self.assertEqual(record["user_result"],1.0)
                self.assertEqual(record["opening"],"King's Pawn Game · C20")
                self.assertTrue(Path(record["pgn_path"]).is_file())
                db=store.connect()
                try:db.execute("UPDATE chesscom_games SET opening='A02' WHERE game_id=?",(game_id,));db.commit()
                finally:db.close()
                self.assertEqual(store.chesscom_games("player1")[0]["opening"],"Bird Opening · A02")
                review='[{"ply":1}]'
                store.set_chesscom_review(game_id,True,91.0,1530,review,'{"error_themes":{}}')
                store.save_chesscom_game("player1",game)
                redownloaded=store.chesscom_games("player1")
                self.assertEqual(len(redownloaded),1)
                self.assertTrue(redownloaded[0]["reviewed"])
                self.assertEqual(redownloaded[0]["review_json"],review)
                self.assertEqual(redownloaded[0]["opening"],"King's Pawn Game · C20")
                prepared=chess.pgn.read_game(io.StringIO(prepare_chesscom_pgn(redownloaded[0])))
                self.assertEqual(prepared.headers["OfflineChessUserColor"],"White")
                self.assertEqual(prepared.headers["OfflineChessPlayedLocally"],"0")
                store.clear_pgns()
                cleared=store.chesscom_games("player1")[0]
                self.assertEqual(cleared["pgn_path"],"")
                self.assertTrue(cleared["reviewed"])
                self.assertEqual(cleared["review_json"],review)
                self.assertFalse(Path(record["pgn_path"]).exists())
            finally:
                if previous is None:os.environ.pop("LOCALAPPDATA",None)
                else:os.environ["LOCALAPPDATA"]=previous

    def test_chesscom_performance_estimate_requires_eight_rated_results(self):
        games=[{"rated":True,"opponent_rating":1500,"user_result":score}
               for score in (1,0,.5,1,0,.5,1,0)]
        self.assertIsNone(predicted_performance_elo(games[:7]))
        estimate=predicted_performance_elo(games)
        self.assertIsNotNone(estimate)
        self.assertGreaterEqual(estimate,1400)
        self.assertLessEqual(estimate,1600)

    def test_completed_chesscom_review_saves_to_its_downloaded_game(self):
        previous=os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as data_dir:
            os.environ["LOCALAPPDATA"]=data_dir
            try:
                store=ProfileStore()
                pgn='[Event "Online"]\n[White "player1"]\n[Black "opponent"]\n[Result "1-0"]\n[ECO "A02"]\n\n1. f4 d5 1-0\n'
                game={"url":"https://www.chess.com/game/live/review-save","end_time":1793450000,
                      "rated":True,"time_control":"600+5","time_class":"rapid","rules":"chess","pgn":pgn,
                      "white":{"username":"player1","rating":1500,"result":"win"},
                      "black":{"username":"opponent","rating":1480,"result":"checkmated"}}
                game_id=store.save_chesscom_game("player1",game)
                item=ReviewMove(1,"e4","e4","Best","White",0,25,"e4 e5","Strong central move.",
                                "White","Out of book",0,"Opening",94.0,chess.Move.from_uci("e2e4"))
                class ReviewHarness:
                    profile_store=store
                    player_color=chess.WHITE
                    external_review_game_id=game_id
                    external_review_username="player1"
                    syzygy_active=False
                    _saved_game_stats_cache=["stale"]
                    def _review_stats(self,_moves):
                        return {"analysis_version":REVIEW_ANALYSIS_VERSION,"error_themes":{}}
                harness=ReviewHarness()
                ChessWindow._save_review_to_profile(harness,[item])
                saved=store.chesscom_games("player1")[0]
                self.assertTrue(saved["reviewed"])
                self.assertEqual(saved["user_accuracy"],94.0)
                self.assertIsNone(saved["predicted_elo"])
                self.assertEqual(harness._saved_game_stats_cache,None)
                restored=deserialize_review_moves(saved["review_json"])
                self.assertEqual([(move.ply,move.san) for move in restored],[(1,"e4")])
                self.assertEqual(json.loads(saved["stats_json"])["analysis_version"],REVIEW_ANALYSIS_VERSION)
            finally:
                if previous is None:os.environ.pop("LOCALAPPDATA",None)
                else:os.environ["LOCALAPPDATA"]=previous

    def test_profile_dialog_exposes_chesscom_download_and_review_controls(self):
        app=QApplication.instance() or QApplication([])
        previous=os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as data_dir:
            os.environ["LOCALAPPDATA"]=data_dir
            try:
                store=ProfileStore()
                opened=[]
                dialog=ProfileDialog(store,opened.append)
                self.assertEqual(dialog.tabs.tabText(1),"Chess.com")
                self.assertGreaterEqual(len(BOARD_THEMES),12)
                self.assertGreaterEqual(len(PIECE_STYLES),5)
                self.assertIn("nordic",[dialog.theme_choice.itemData(i) for i in range(dialog.theme_choice.count())])
                self.assertIn("badge",[dialog.piece_choice.itemData(i) for i in range(dialog.piece_choice.count())])
                dialog.theme_choice.setCurrentIndex(dialog.theme_choice.findData("nordic"))
                dialog.piece_choice.setCurrentIndex(dialog.piece_choice.findData("letters"))
                self.assertEqual(dialog.theme_preview.theme_key,"nordic")
                self.assertEqual(dialog.theme_preview.piece_style,"letters")
                buttons={button.text() for button in dialog.findChildren(QPushButton)}
                self.assertIn("Download all months",buttons)
                self.assertIn("Download this month",buttons)
                self.assertIn("Review selected",buttons)
                self.assertIn("Review all unreviewed",buttons)
                self.assertIn("Open PGN folder",buttons)
                self.assertEqual(dialog.chesscom_library.horizontalHeader().sectionResizeMode(0),
                                 QHeaderView.ResizeMode.ResizeToContents)
                self.assertEqual(dialog.chesscom_library.horizontalScrollBarPolicy(),
                                 Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                pgn='[Event "Online"]\n[White "player1"]\n[Black "opponent"]\n[Result "1-0"]\n[ECO "A02"]\n\n1. f4 d5 1-0\n'
                game={"url":"https://www.chess.com/game/live/profile-ui","end_time":1793450000,
                      "rated":True,"time_control":"600+5","time_class":"rapid","rules":"chess","pgn":pgn,
                      "white":{"username":"player1","rating":1500,"result":"win"},
                      "black":{"username":"opponent","rating":1480,"result":"checkmated"}}
                dialog.chesscom_username.setText("player1")
                dialog._chesscom_month_loaded([game])
                self.assertEqual(dialog.chesscom_available.rowCount(),1)
                dialog.chesscom_available.selectRow(0)
                dialog.download_selected_chesscom()
                self.assertEqual(dialog.chesscom_library.rowCount(),1)
                self.assertEqual(dialog.chesscom_library.item(0,5).text(),"Bird Opening · A02")
                self.assertEqual(dialog.chesscom_openings.item(0,0).text(),"Bird Opening · A02")
                self.assertIn("Most played opening: Bird Opening · A02",dialog.chesscom_trends.text())
                self.assertTrue(Path(store.chesscom_games("player1")[0]["pgn_path"]).is_file())
                blitz_game=dict(game);blitz_game.update(url="https://www.chess.com/game/live/profile-blitz",
                    time_control="300",time_class="blitz",
                    pgn='[Event "Online"]\n[White "player1"]\n[Black "opponent"]\n[Result "0-1"]\n[ECO "C20"]\n\n1. e4 e5 0-1\n')
                store.save_chesscom_game("player1",blitz_game)
                dialog.refresh_chesscom_library("player1")
                self.assertEqual(dialog.chesscom_library.rowCount(),2)
                dialog.chesscom_speed_filter.setCurrentText("Blitz")
                self.assertEqual(dialog.chesscom_library.rowCount(),1)
                self.assertEqual(dialog.chesscom_openings.item(0,0).text(),"King's Pawn Game · C20")
                self.assertIn("Showing 1 of 2 downloaded games",dialog.chesscom_trends.text())
                dialog.chesscom_speed_filter.setCurrentText("Rapid")
                self.assertEqual(dialog.chesscom_library.rowCount(),1)
                self.assertEqual(dialog.chesscom_openings.item(0,0).text(),"Bird Opening · A02")
                dialog.chesscom_library.selectRow(0)
                dialog.review_selected_chesscom()
                self.assertEqual(opened[-1]["chesscom_batch"][0]["opponent"],"opponent")
                all_opened=[]
                second=ProfileDialog(store,all_opened.append)
                second.chesscom_username.setText("player1")
                second.review_unreviewed_chesscom()
                self.assertEqual(all_opened[0]["chesscom_batch"][0]["opponent"],"opponent")
                second.close();second.deleteLater()
                dialog.close();dialog.deleteLater();app.processEvents()
            finally:
                if previous is None:os.environ.pop("LOCALAPPDATA",None)
                else:os.environ["LOCALAPPDATA"]=previous

    def test_profile_store_migrates_existing_database_for_cached_reviews(self):
        previous=os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as data_dir:
            os.environ["LOCALAPPDATA"]=data_dir
            try:
                profile_dir=os.path.join(data_dir,"OfflineChess");os.makedirs(profile_dir)
                db_path=os.path.join(profile_dir,"profile.sqlite3")
                db=sqlite3.connect(db_path)
                try:
                    db.execute("""CREATE TABLE games(
                        game_key TEXT PRIMARY KEY, played_at TEXT NOT NULL, result TEXT NOT NULL,
                        user_color TEXT NOT NULL, user_result REAL, opponent_elo INTEGER,
                        elo_approx INTEGER NOT NULL, user_accuracy REAL, white_accuracy REAL,
                        black_accuracy REAL, plies INTEGER NOT NULL, opening TEXT NOT NULL,
                        average_loss REAL, pgn TEXT, stats_json TEXT NOT NULL DEFAULT '{}')""")
                    db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                finally:db.close()
                store=ProfileStore()
                db=store.connect()
                try:columns={row[1] for row in db.execute("PRAGMA table_info(games)")}
                finally:db.close()
                self.assertIn("review_json",columns)
                record={"game_key":"migration-check","played_at":"2026-11-01T12:00",
                        "result":"*","user_color":"White","user_result":None,
                        "opponent_elo":None,"elo_approx":1,"user_accuracy":90.0,
                        "white_accuracy":90.0,"black_accuracy":None,"plies":1,
                        "opening":"Unknown","average_loss":0.0,"pgn":"1. e4 *",
                        "stats_json":"{}","review_json":"[]"}
                store.save_review(record)
                self.assertEqual(store.games()[0]["review_json"],"[]")
            finally:
                if previous is None:os.environ.pop("LOCALAPPDATA",None)
                else:os.environ["LOCALAPPDATA"]=previous

    def test_personalized_coach_only_mentions_themes_repeated_across_games(self):
        one={"error_themes":{"Undefended piece exposed":1}}
        two={"error_themes":{"Undefended piece exposed":2}}
        items=[]
        self.assertNotIn("saved reviews",personalized_review_coach(items,chess.WHITE,[one]))
        coach=personalized_review_coach(items,chess.WHITE,[one,two])
        self.assertIn("the recurring pattern is “Undefended piece exposed”",coach)
        self.assertIn("2 separate games",coach)

    def test_saved_review_round_trip_preserves_move_lines_and_events(self):
        board=chess.Board();move=board.parse_san("e4")
        item=ReviewMove(1,"e4","e4","Best","White",0,25,"e4 e5","Develops toward the center.",
                        "White","Book not loaded",0,"Opening",99.2,move,
                        best_pv_moves=(move,),events=(ReviewEvent("center_control","Controls the center",focus_squares=(chess.E4,)),),
                        opponent_move=board.parse_san("d4"),acceptable_moves=(move,),alternative_sans=("d4",),move_number=1)
        restored=deserialize_review_moves(serialize_review_moves([item]))[0]
        self.assertEqual(restored.san,item.san)
        self.assertEqual(restored.best_move,item.best_move)
        self.assertEqual(restored.best_pv_moves,item.best_pv_moves)
        self.assertEqual(restored.events,item.events)
        self.assertEqual(restored.opponent_move,item.opponent_move)
        self.assertEqual(restored.alternative_sans,item.alternative_sans)

    def test_mate_score_direction_is_relative_to_the_player(self):
        self.assertTrue(engine_line_forces_mate(chess.engine.PovScore(chess.engine.Mate(3),chess.WHITE),chess.WHITE))
        self.assertFalse(engine_line_forces_mate(chess.engine.PovScore(chess.engine.Mate(-3),chess.WHITE),chess.WHITE))
        self.assertTrue(engine_line_forces_mate(chess.engine.PovScore(chess.engine.Mate(3),chess.BLACK),chess.BLACK))

    def test_retry_attempt_feedback_uses_engine_reply_and_compares_to_best(self):
        class PracticeEngine:
            def analyse(self,board,_limit,**kwargs):
                if board.piece_at(chess.E4):
                    reply=board.parse_san("e5")
                    score=chess.engine.PovScore(chess.engine.Cp(20),chess.WHITE)
                else:
                    reply=board.parse_san("d5")
                    score=chess.engine.PovScore(chess.engine.Cp(-220),chess.WHITE)
                return {"score":score,"pv":[reply]}
        board=chess.Board();best=board.parse_san("e4");attempt=board.parse_san("d4")
        worker=EngineWorker();worker.engine=PracticeEngine();worker.option_names=set()
        results=[];worker.practice_analysis_done.connect(results.append)
        worker.analyze_practice_attempt((41,board,attempt,best,.12))
        self.assertEqual(results[0]["token"],41)
        self.assertIn("Strongest reply: d5",results[0]["feedback"])
        self.assertIn("expected score",results[0]["feedback"])
        self.assertIn("compared with",results[0]["feedback"])

    def test_review_worker_finishes_a_64_ply_game_with_cached_analysis_fields(self):
        class FakeEngine:
            def analyse(self,board,_limit,**kwargs):
                legal=list(board.legal_moves)
                lines=[{"score":chess.engine.PovScore(chess.engine.Cp(0),chess.WHITE),"pv":[move]} for move in legal[:kwargs.get("multipv",1)]]
                return lines if kwargs.get("multipv",1)>1 else lines[0]

        board=chess.Board();moves=[]
        for san in ("Nf3","Nf6","Ng1","Ng8")*16:
            move=board.parse_san(san);moves.append(move);board.push(move)
        worker=EngineWorker();worker.engine=FakeEngine();worker.option_names=set();worker._multipv_max=1
        completed=[];worker.review_done.connect(lambda token,items,cancelled:completed.append((token,items,cancelled)))
        worker.review_game((27,chess.Board(),moves,None,.01))
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0][0],27)
        self.assertFalse(completed[0][2])
        self.assertEqual(len(completed[0][1]),len(moves))
        first=completed[0][1][0]
        self.assertTrue(first.best_pv_moves)
        self.assertIsInstance(first.eval_before,int)

    def test_missing_forced_mate_is_flagged_even_when_expected_points_barely_change(self):
        board=position([(chess.WHITE,chess.KING,"f6"),(chess.WHITE,chess.QUEEN,"g6"),
                        (chess.BLACK,chess.KING,"h8")])
        played=board.parse_san("Qg5");best=board.parse_san("Qg7#")
        class MateEngine:
            def analyse(self,position,_limit,**kwargs):
                if position.turn==chess.WHITE:
                    return {"score":chess.engine.PovScore(chess.engine.Mate(1),chess.WHITE),"pv":[best]}
                reply=next(iter(position.legal_moves))
                return {"score":chess.engine.PovScore(chess.engine.Cp(3000),chess.WHITE),"pv":[reply]}
        worker=EngineWorker();worker.engine=MateEngine();worker.option_names=set();worker._multipv_max=1
        completed=[];errors=[];worker.review_done.connect(lambda token,items,cancelled:completed.append((token,items,cancelled)))
        worker.operation_error.connect(errors.append)
        worker.review_game((28,board,[played],None,.01))
        self.assertFalse(errors,errors)
        self.assertEqual(len(completed),1)
        item=completed[0][1][0]
        self.assertEqual(item.verdict,"Miss")
        self.assertTrue(any(event.kind=="missed_mate" for event in item.events))

    def test_bishop_move_removing_queen_defender_names_the_capture(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.BLACK,chess.KING,"e8"),
            (chess.WHITE,chess.QUEEN,"a7"),(chess.WHITE,chess.BISHOP,"b6"),
            (chess.BLACK,chess.ROOK,"a8"),
        ])
        move=board.parse_san("Bc7");after=board.copy();after.push(move)
        reply=after.parse_san("Rxa7")
        events=analyze_review_events(board,move,after,move,reply)
        found=next(event for event in events if event.kind=="lost_defender")
        self.assertIn("only defender",found.text)
        self.assertIn("queen on a7",found.text)
        self.assertIn("Rxa7",found.text)

    def test_undefended_rook_is_identified_by_piece_and_square(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.BLACK,chess.KING,"e8"),
            (chess.WHITE,chess.ROOK,"a7"),(chess.WHITE,chess.BISHOP,"b6"),
            (chess.BLACK,chess.QUEEN,"a8"),
        ])
        move=board.parse_san("Bc7");after=board.copy();after.push(move)
        reply=after.parse_san("Qxa7")
        events=analyze_review_events(board,move,after,move,reply)
        self.assertTrue(any(event.kind=="lost_defender" and "rook on a7" in event.text for event in events))

    def test_trapped_knight_has_no_safe_retreat(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.BLACK,chess.KING,"h8"),
            (chess.WHITE,chess.KNIGHT,"b6"),(chess.BLACK,chess.ROOK,"a7"),
            (chess.BLACK,chess.QUEEN,"d8"),
        ])
        move=board.parse_san("Na8");after=board.copy();after.push(move)
        reply=after.parse_san("Rxa8")
        events=analyze_review_events(board,move,after,move,reply)
        self.assertTrue(any(event.kind=="trapped_piece" and "knight on a8" in event.text for event in events))

    def test_move_that_opens_a_bishop_line_marks_new_pin(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.BLACK,chess.KING,"f8"),
            (chess.WHITE,chess.BISHOP,"a3"),(chess.WHITE,chess.PAWN,"b4"),
            (chess.BLACK,chess.KNIGHT,"e7"),
        ])
        move=board.parse_san("b5");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        self.assertTrue(any(event.kind=="new_pin" and "knight on e7" in event.text for event in events))
        prompt=review_reflection_prompt(events)
        self.assertIn("bishop on a3",prompt)
        self.assertIn("knight on e7",prompt)

    def test_move_that_opens_a_relative_pin_names_the_valuable_target(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.WHITE,chess.BISHOP,"a3"),
            (chess.WHITE,chess.PAWN,"b4"),(chess.BLACK,chess.KING,"h8"),
            (chess.BLACK,chess.KNIGHT,"e7"),(chess.BLACK,chess.ROOK,"f8"),
        ])
        move=board.parse_san("b5");after=board.copy();after.push(move)
        self.assertIn("Pin",tactical_themes(board,move))
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="new_pin")
        self.assertIn("knight on e7",event.text)
        self.assertIn("rook on f8",event.text)
        self.assertIn("rook on f8",review_reflection_prompt(events))

        played=board.parse_san("Ka2");played_after=board.copy();played_after.push(played)
        missed=analyze_review_events(board,played,played_after,move)
        missed_pin=next(item for item in missed if item.kind=="missed_tactic")
        self.assertIn("knight on e7",missed_pin.text)
        self.assertIn("rook on f8",missed_pin.text)

    def test_discovered_attack_names_the_slider_and_target(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.WHITE,chess.KNIGHT,"a2"),(chess.BLACK,chess.KING,"h8"),
            (chess.BLACK,chess.QUEEN,"a8"),
        ])
        move=board.parse_san("Nb4");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="discovered_attack")
        self.assertIn("rook on a1",event.text)
        self.assertIn("queen on a8",event.text)

    def test_discovered_check_names_the_attacking_slider_and_king(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.WHITE,chess.KNIGHT,"a2"),(chess.BLACK,chess.KING,"a8"),
        ])
        move=board.parse_san("Nb4");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="discovered_check")
        self.assertIn("rook on a1",event.text)
        self.assertIn("king on a8",event.text)

    def test_skewer_explanation_names_the_attacker_and_both_targets(self):
        board=position([
            (chess.WHITE,chess.KING,"a2"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.BLACK,chess.KING,"h8"),(chess.BLACK,chess.QUEEN,"f1"),
            (chess.BLACK,chess.ROOK,"h1"),
        ])
        move=board.parse_san("Rd1");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="skewer")
        self.assertIn("rook on d1",event.text)
        self.assertIn("queen on f1",event.text)
        self.assertIn("rook on h1",event.text)

    def test_missed_skewer_names_the_attacker_and_both_targets(self):
        board=position([
            (chess.WHITE,chess.KING,"a2"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.BLACK,chess.KING,"h8"),(chess.BLACK,chess.QUEEN,"f1"),
            (chess.BLACK,chess.ROOK,"h1"),
        ])
        played=board.parse_san("Ka3");after=board.copy();after.push(played)
        best=board.parse_san("Rd1")
        events=analyze_review_events(board,played,after,best)
        event=next(item for item in events if item.kind=="missed_tactic")
        self.assertIn("rook on d1",event.text)
        self.assertIn("queen on f1",event.text)
        self.assertIn("rook on h1",event.text)
        prompt=review_reflection_prompt((event,))
        self.assertIn("queen on f1",prompt)
        self.assertIn("rook on h1",prompt)

    def test_missed_pin_names_the_piece_and_pinning_slider(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.BLACK,chess.KING,"f8"),
            (chess.WHITE,chess.BISHOP,"a3"),(chess.WHITE,chess.PAWN,"b4"),
            (chess.BLACK,chess.KNIGHT,"e7"),
        ])
        played=board.parse_san("Ka2");after=board.copy();after.push(played)
        best=board.parse_san("b5")
        events=analyze_review_events(board,played,after,best)
        event=next(item for item in events if item.kind=="missed_tactic")
        self.assertIn("knight on e7",event.text)
        self.assertIn("bishop on a3",event.text)
        prompt=review_reflection_prompt(events)
        self.assertIn("knight on e7",prompt)
        self.assertIn("king on f8",prompt)

    def test_fork_and_missed_mate_are_reported_from_actual_moves(self):
        fork_board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.WHITE,chess.KNIGHT,"g5"),
            (chess.BLACK,chess.KING,"e5"),(chess.BLACK,chess.QUEEN,"d8"),
            (chess.BLACK,chess.ROOK,"h8"),
        ])
        fork=fork_board.parse_san("Nf7+");fork_after=fork_board.copy();fork_after.push(fork)
        fork_events=analyze_review_events(fork_board,fork,fork_after,fork)
        self.assertTrue(any(event.kind=="fork" and "queen on d8" in event.text for event in fork_events))
        played=fork_board.parse_san("Ka2");played_after=fork_board.copy();played_after.push(played)
        missed=analyze_review_events(fork_board,played,played_after,fork)
        missed_fork=next(event for event in missed if event.kind=="missed_tactic" and "fork" in event.text)
        self.assertIn("king on e5",missed_fork.text)
        self.assertIn("queen on d8",missed_fork.text)
        prompt=review_reflection_prompt(missed)
        self.assertIn("king on e5",prompt)
        self.assertIn("queen on d8",prompt)
        near_equal=analyze_review_events(fork_board,played,played_after,fork,explain_best_difference=False)
        self.assertFalse(any(event.kind in ("missed_tactic","missed_material","missed_plan") for event in near_equal))

        mate_board=position([
            (chess.WHITE,chess.KING,"f6"),(chess.WHITE,chess.QUEEN,"g6"),
            (chess.BLACK,chess.KING,"h8"),
        ])
        played=mate_board.parse_san("Ke5");after=mate_board.copy();after.push(played)
        best=mate_board.parse_san("Qg7#")
        mate_events=analyze_review_events(mate_board,played,after,best,explain_best_difference=False)
        self.assertTrue(any(event.kind=="missed_mate" for event in mate_events))

    def test_rook_rook_fork_is_recognized_and_explained(self):
        board=position([
            (chess.WHITE,chess.KING,"h1"),(chess.WHITE,chess.KNIGHT,"b3"),
            (chess.BLACK,chess.KING,"h8"),(chess.BLACK,chess.ROOK,"a4"),
            (chess.BLACK,chess.ROOK,"e4"),
        ])
        move=board.parse_san("Nc5");after=board.copy();after.push(move)
        self.assertIn("Fork",tactical_themes(board,move))
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="fork")
        self.assertIn("rook on a4",event.text)
        self.assertIn("rook on e4",event.text)

    def test_missed_non_mating_check_names_the_checking_piece_and_king(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.QUEEN,"d1"),
            (chess.BLACK,chess.KING,"e8"),
        ])
        played=board.parse_san("Kd2");after=board.copy();after.push(played)
        best=board.parse_san("Qh5+")
        events=analyze_review_events(board,played,after,best)
        event=next(item for item in events if item.kind=="missed_tactic")
        self.assertIn("queen on h5",event.text)
        self.assertIn("king on e8",event.text)
        self.assertIn("king on e8",event.prompt)
        self.assertIn(chess.E8,review_focus_squares(events))

    def test_missed_discovered_attack_names_the_line_piece_and_target(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.WHITE,chess.KNIGHT,"a2"),(chess.BLACK,chess.KING,"h8"),
            (chess.BLACK,chess.QUEEN,"a8"),
        ])
        played=board.parse_san("Kf1");after=board.copy();after.push(played)
        best=board.parse_san("Nb4")
        events=analyze_review_events(board,played,after,best)
        event=next(item for item in events if item.kind=="missed_tactic")
        self.assertIn("rook on a1",event.text)
        self.assertIn("queen on a8",event.text)
        self.assertIn("queen on a8",event.prompt)
        self.assertIn(chess.A8,review_focus_squares(events))

    def test_missed_promotion_names_the_pawn_and_promoted_piece(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.WHITE,chess.PAWN,"g7"),
            (chess.BLACK,chess.KING,"h8"),
        ])
        played=board.parse_san("Ka2");after=board.copy();after.push(played)
        best=board.parse_san("g8=Q+")
        events=analyze_review_events(board,played,after,best)
        event=next(item for item in events if item.kind=="missed_tactic")
        self.assertIn("pawn on g7",event.text)
        self.assertIn("queen on g8",event.text)
        self.assertIn("king on h8",event.text)

    def test_capture_and_missed_material_explanations_name_piece_and_square(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.WHITE,chess.KNIGHT,"c3"),
            (chess.BLACK,chess.KING,"h8"),(chess.BLACK,chess.ROOK,"d5"),
        ])
        best=board.parse_san("Nxd5");after=board.copy();after.push(best)
        capture_events=analyze_review_events(board,best,after,best)
        self.assertTrue(any(event.kind=="capture" and "rook on d5" in event.text for event in capture_events))
        played=board.parse_san("Ka2");played_after=board.copy();played_after.push(played)
        missed=analyze_review_events(board,played,played_after,best)
        self.assertTrue(any(event.kind=="missed_material" and "rook on d5" in event.text for event in missed))

    def test_en_passant_capture_message_names_the_captured_pawn_square(self):
        board=position([
            (chess.WHITE,chess.KING,"a1"),(chess.WHITE,chess.PAWN,"e5"),
            (chess.BLACK,chess.KING,"h8"),(chess.BLACK,chess.PAWN,"d5"),
        ])
        board.ep_square=chess.D6
        move=board.parse_san("exd6");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        self.assertTrue(any(event.kind=="capture" and "pawn on d5" in event.text for event in events))

    def test_back_rank_reply_reports_checkmate(self):
        board=position([
            (chess.WHITE,chess.KING,"g1"),(chess.WHITE,chess.PAWN,"f2"),
            (chess.WHITE,chess.PAWN,"g2"),(chess.WHITE,chess.PAWN,"h2"),
            (chess.WHITE,chess.PAWN,"a2"),(chess.BLACK,chess.KING,"a8"),
            (chess.BLACK,chess.ROOK,"g8"),(chess.BLACK,chess.QUEEN,"g3"),
        ])
        move=board.parse_san("a3");after=board.copy();after.push(move)
        reply=after.parse_san("Qxg2#")
        events=analyze_review_events(board,move,after,move,reply)
        self.assertTrue(any(event.kind=="back_rank_mate" for event in events))

    def test_positional_center_loss_is_explained_without_claiming_material_loss(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.KNIGHT,"c3"),
            (chess.BLACK,chess.KING,"e8"),
        ])
        move=board.parse_san("Nb1");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,board.parse_san("Nd5"))
        event=next(item for item in events if item.kind=="center_control")
        commentary=generate_review_commentary("Mistake","Nb1","Nd5",20,-40,60,events)
        self.assertIn("central squares",event.text)
        self.assertIn("central squares",commentary)
        self.assertNotIn("loses material",commentary)

    def test_quiet_development_move_gets_a_concrete_center_control_explanation(self):
        board=position([(chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.KNIGHT,"b1"),
                        (chess.BLACK,chess.KING,"e8")])
        move=board.parse_san("Nc3");after=board.copy();after.push(move)
        events=analyze_review_events(board,move,after,move)
        event=next(item for item in events if item.kind=="central_control")
        commentary=generate_review_commentary("Best","Nc3","Nc3",0,0,0,events)
        self.assertIn("central squares",event.text)
        self.assertIn("central squares",commentary)

    def test_missed_quiet_move_names_the_engine_alternatives_positional_gain(self):
        board=chess.Board();played=board.parse_san("a3");after=board.copy();after.push(played)
        best=board.parse_san("e4")
        events=analyze_review_events(board,played,after,best)
        plan=next(event for event in events if event.kind=="missed_plan")
        self.assertIn("e4",plan.text)
        self.assertIn("d5",plan.text)
        prompt=review_reflection_prompt(events)
        self.assertIn("d5",prompt)
        commentary=generate_review_commentary("Inaccuracy","a3","e4",20,-70,90,events)
        self.assertIn("d5",commentary)
        self.assertNotIn("evaluation-based warning",commentary)

    def test_engine_line_confirms_when_a_best_move_removes_a_queen_defender(self):
        board=position([
            (chess.WHITE,chess.KING,"h1"),(chess.WHITE,chess.BISHOP,"a1"),
            (chess.WHITE,chess.ROOK,"d1"),(chess.BLACK,chess.KING,"a8"),
            (chess.BLACK,chess.BISHOP,"d4"),(chess.BLACK,chess.QUEEN,"g7"),
        ])
        best=board.parse_san("Bxd4")
        line=board.copy();line.push(best)
        queen_capture=line.parse_san("Qxd4");line.push(queen_capture)
        rook_wins_queen=line.parse_san("Rxd4")
        event=removed_defender_event(board,best,(best,queen_capture,rook_wins_queen))
        self.assertIsNotNone(event)
        self.assertIn("only defender",event.text)
        self.assertIn("queen on g7",event.text)
        self.assertIn("Bxd4 Qxd4 Rxd4",event.text)
        self.assertEqual(event.severity,"warning")
        after_best=board.copy();after_best.push(best)
        events=analyze_review_events(board,best,after_best,best,queen_capture,
                                     (best,queen_capture,rook_wins_queen),(0),
                                     (rook_wins_queen,),False)
        positive=next(event for event in events if event.kind=="removed_defender")
        self.assertEqual(positive.severity,"positive")
        self.assertIn("Your move Bxd4",positive.text)

    def test_game_story_prioritizes_the_actual_checkmate_finish(self):
        earlier=SimpleNamespace(side="Black",ply=17,move_number=9,san="Qh5",verdict="Blunder",
                                expected_loss=.40,events=(),best_san="Nf6")
        finish=SimpleNamespace(side="White",ply=42,move_number=21,san="Qh7#",verdict="Best",
                               expected_loss=0,events=(ReviewEvent("checkmate","Qh7# checkmates.","positive"),))
        story=review_game_narrative([earlier,finish])
        self.assertIn("game ended in checkmate",story)
        self.assertIn("21. Qh7# by White",story)

    def test_board_focus_prefers_urgent_event_targets(self):
        urgent=ReviewEvent("lost_defender","Rook on a1 is undefended.","critical",focus_squares=(chess.A1,))
        tactic=ReviewEvent("skewer","Rook on d1 attacks the queen and rook.","positive",focus_squares=(chess.D1,chess.F1,chess.H1))
        self.assertEqual(review_focus_squares((tactic,urgent)),(chess.A1,))
        self.assertEqual(review_focus_squares((tactic,)),(chess.D1,chess.F1,chess.H1))

    def test_sacrifice_requires_best_move_and_engine_support(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.BLACK,chess.KING,"e8"),(chess.BLACK,chess.PAWN,"a2"),
            (chess.BLACK,chess.BISHOP,"b3"),
        ])
        move=board.parse_san("Rxa2")
        self.assertTrue(is_engine_justified_sacrifice(board,move,move,(move,),.70,.72,True))
        self.assertTrue(is_engine_justified_sacrifice(board,move,move,(move,),.70,.72,False))
        self.assertFalse(is_engine_justified_sacrifice(board,move,board.parse_san("Rb1"),(move,),.70,.72,True))
        self.assertFalse(is_engine_justified_sacrifice(board,move,move,(move,),.985,.99,True))

    def test_equal_rook_exchange_is_not_mislabeled_as_a_sacrifice(self):
        board=chess.Board("2r2rk1/R4ppp/Qbq1p1b1/1pp1P3/4PB2/6P1/PP3PBP/R5K1 w - - 7 24")
        position=board.copy();line=[]
        for san in ("Rb7","Rb8","Rxb8","Rxb8"):
            move=position.parse_san(san);line.append(move);position.push(move)
        self.assertEqual(board.variation_san(line),"24. Rb7 Rb8 25. Rxb8 Rxb8")
        self.assertFalse(is_engine_justified_sacrifice(board,line[0],line[0],tuple(line),.70,.72,False))

    def test_capturing_a_different_piece_does_not_prove_a_queen_sacrifice(self):
        board=chess.Board("r1b2rk1/3nbpp1/pq5p/1pp1N3/3pPB2/PN1P3P/1PQ2PP1/2R2RK1 b - - 5 19")
        move=board.parse_san("Qf6")
        reply_board=board.copy();reply_board.push(move)
        reply=reply_board.parse_san("Nxd7")
        self.assertFalse(is_engine_justified_sacrifice(
            board,move,move,(move,reply),.60,.60,False))

    def test_review_worker_does_not_call_a_queen_move_brilliant_for_a_knight_loss(self):
        board=chess.Board("r1b2rk1/3nbpp1/pq5p/1pp1N3/3pPB2/PN1P3P/1PQ2PP1/2R2RK1 b - - 5 19")
        move=board.parse_san("Qf6");after=board.copy();after.push(move)
        other=next(candidate for candidate in board.legal_moves if candidate!=move)
        reply=after.parse_san("Nxd7")
        class Engine:
            def analyse(self,position,_limit,multipv=None):
                if position.fen()==board.fen():
                    lines=[{"score":chess.engine.PovScore(chess.engine.Cp(20),chess.WHITE),
                            "pv":[move,reply]},
                           {"score":chess.engine.PovScore(chess.engine.Cp(-80),chess.WHITE),
                            "pv":[other]}]
                    return lines[:multipv] if multipv else lines[0]
                return {"score":chess.engine.PovScore(chess.engine.Cp(20),chess.WHITE),"pv":[reply]}
        worker=EngineWorker();worker.engine=Engine();worker.option_names={"MultiPV"};worker._multipv_max=3
        completed=[];worker.review_done.connect(lambda token,items,cancelled:completed.extend(items))
        worker.review_game((39,board,[move],None,.55))
        self.assertNotEqual(completed[0].verdict,"Brilliant")

    def test_fork_shape_without_a_calculated_payoff_does_not_prove_sacrifice(self):
        board=position([
            (chess.WHITE,chess.KING,"h1"),(chess.WHITE,chess.KNIGHT,"c3"),
            (chess.BLACK,chess.KING,"e7"),(chess.BLACK,chess.QUEEN,"f6"),
            (chess.BLACK,chess.PAWN,"d5"),
        ])
        move=board.parse_san("Nxd5")
        self.assertFalse(is_engine_justified_sacrifice(board,move,move,(move,),.70,.70,False))

    def test_brilliant_label_requires_thorough_nonbook_unforced_confirmation(self):
        self.assertTrue(can_label_brilliant(True,.55,False,20,True))
        rejected=(
            (False,.55,False,20),
            (True,.39,False,20),
            (True,.55,True,20),
            (True,.55,False,1),
            (True,.55,False,20,False),
        )
        for args in rejected:
            with self.subTest(args=args):
                self.assertFalse(can_label_brilliant(*args))

    def test_brilliant_requires_a_clear_multipv_gap_over_the_runner_up(self):
        board=chess.Board();played=board.parse_san("e4");alternative=board.parse_san("d4")
        lines=[{"pv":[played],"score":chess.engine.PovScore(chess.engine.Cp(150),chess.WHITE)},
               {"pv":[alternative],"score":chess.engine.PovScore(chess.engine.Cp(0),chess.WHITE)}]
        gap=brilliant_choice_gap(board,lines,chess.WHITE,played)
        self.assertGreaterEqual(gap,.04)
        self.assertTrue(can_label_brilliant(True,.55,False,20,gap>=.04))
        tied=[lines[0],{"pv":[alternative],"score":chess.engine.PovScore(chess.engine.Cp(145),chess.WHITE)}]
        tied_gap=brilliant_choice_gap(board,tied,chess.WHITE,played)
        self.assertLess(tied_gap,.04)
        self.assertEqual(tied_gap,0.0)
        self.assertFalse(can_label_brilliant(True,.55,False,20,tied_gap>=.04))

    def test_only_good_move_requires_all_legal_moves_and_a_clear_gap(self):
        board=position([(chess.WHITE,chess.KING,"e1"),(chess.BLACK,chess.KING,"e8")])
        played=board.parse_san("Kd2");moves=list(board.legal_moves)
        ordered=[played]+[move for move in moves if move!=played]
        lines=[{"pv":[move],"score":chess.engine.PovScore(
            chess.engine.Cp(150 if move==played else -450-index*50),chess.WHITE)}
            for index,move in enumerate(ordered)]
        gap=only_good_move_gap(board,lines,chess.WHITE,played,len(moves),.57)
        self.assertGreaterEqual(gap,.15)
        self.assertEqual(only_good_move_gap(board,lines[:-1],chess.WHITE,played,len(moves),.57),0.0)
        close=[{"pv":[move],"score":chess.engine.PovScore(
            chess.engine.Cp(150 if move==played else 120),chess.WHITE)} for move in ordered]
        self.assertEqual(only_good_move_gap(board,close,chess.WHITE,played,len(moves),.57),0.0)

    def test_review_worker_marks_a_proven_only_good_move_as_great(self):
        board=position([(chess.WHITE,chess.KING,"e1"),(chess.BLACK,chess.KING,"e8")])
        played=board.parse_san("Kd2");moves=list(board.legal_moves)
        ordered=[played]+[move for move in moves if move!=played]
        class OnlyGoodEngine:
            def analyse(self,position,_limit,**kwargs):
                if position.turn==chess.WHITE:
                    lines=[{"score":chess.engine.PovScore(
                                chess.engine.Cp(150 if move==played else -450-index*50),chess.WHITE),
                            "pv":[move]} for index,move in enumerate(ordered)]
                    return lines[:kwargs.get("multipv",1)] if kwargs.get("multipv",1)>1 else lines[0]
                return {"score":chess.engine.PovScore(chess.engine.Cp(150),chess.WHITE),
                        "pv":[next(iter(position.legal_moves))]}
        worker=EngineWorker();worker.engine=OnlyGoodEngine();worker.option_names={"MultiPV"};worker._multipv_max=8
        completed=[];worker.review_done.connect(lambda token,items,cancelled:completed.extend(items))
        worker.review_game((77,board,[played],None,.55))
        item=completed[0]
        self.assertEqual(item.verdict,"Great")
        event=next(event for event in item.events if event.kind=="only_good_move")
        self.assertIn("only legal move",event.text)
        self.assertIn("next-best legal move",event.text)
        self.assertIn("only move that held at least an equal position",review_game_narrative(completed))

    def test_review_worker_labels_a_sound_unique_sacrifice_brilliant(self):
        board=position([
            (chess.WHITE,chess.KING,"e1"),(chess.WHITE,chess.ROOK,"a1"),
            (chess.BLACK,chess.KING,"e8"),(chess.BLACK,chess.PAWN,"a2"),
            (chess.BLACK,chess.BISHOP,"b3"),
        ])
        sacrifice=board.parse_san("Rxa2")
        after=board.copy();after.push(sacrifice);recapture=after.parse_san("Bxa2")
        alternative=board.parse_san("Rb1")
        class SacrificeEngine:
            def __init__(self,runner_score,top_score=110,decline=False):
                self.runner_score=runner_score;self.top_score=top_score;self.decline=decline
            def declining_reply(self,position):
                response_board=position.copy()
                if response_board.turn==chess.WHITE:response_board.push(sacrifice)
                return next(move for move in response_board.legal_moves if not response_board.is_capture(move))
            def analyse(self,position,_limit,**kwargs):
                if self.decline:
                    reply=self.declining_reply(position)
                    pv=[sacrifice,reply] if position.turn==chess.WHITE else [reply]
                else:pv=[sacrifice,recapture] if position.turn==chess.WHITE else [recapture]
                score=chess.engine.PovScore(chess.engine.Cp(self.top_score),chess.WHITE)
                if kwargs.get("multipv",1)>1:
                    return [{"score":score,"pv":pv},
                            {"score":chess.engine.PovScore(chess.engine.Cp(self.runner_score),chess.WHITE),"pv":[alternative]}]
                return {"score":score,"pv":pv}
        worker=EngineWorker();worker.engine=SacrificeEngine(0);worker.option_names={"MultiPV"};worker._multipv_max=3
        completed=[];errors=[]
        worker.review_done.connect(lambda token,items,cancelled:completed.append((items,cancelled)))
        worker.operation_error.connect(errors.append)
        worker.review_game((73,board,[sacrifice],None,.55))
        self.assertFalse(errors,errors)
        self.assertEqual(len(completed),1)
        item=completed[0][0][0]
        self.assertEqual(item.verdict,"Brilliant")
        self.assertTrue(any(event.kind=="sound_sacrifice" and "next-best alternative" in event.text for event in item.events))

        positional_worker=EngineWorker();positional_worker.engine=SacrificeEngine(0,decline=True)
        positional_worker.option_names={"MultiPV"};positional_worker._multipv_max=3
        positional=[];positional_worker.review_done.connect(lambda token,items,cancelled:positional.extend(items))
        positional_worker.review_game((76,board,[sacrifice],None,.55))
        self.assertEqual(positional[0].verdict,"Brilliant")

        tied_worker=EngineWorker();tied_worker.engine=SacrificeEngine(105);tied_worker.option_names={"MultiPV"};tied_worker._multipv_max=3
        tied=[];tied_worker.review_done.connect(lambda token,items,cancelled:tied.extend(items))
        tied_worker.review_game((74,board,[sacrifice],None,.55))
        self.assertNotEqual(tied[0].verdict,"Brilliant")

        already_winning_worker=EngineWorker();already_winning_worker.engine=SacrificeEngine(0,4000)
        already_winning_worker.option_names={"MultiPV"};already_winning_worker._multipv_max=3
        already_winning=[];already_winning_worker.review_done.connect(
            lambda token,items,cancelled:already_winning.extend(items))
        already_winning_worker.review_game((75,board,[sacrifice],None,.55))
        self.assertNotEqual(already_winning[0].verdict,"Brilliant")

    def test_forced_book_and_white_black_evaluation_perspectives(self):
        self.assertEqual(override_review_category("Blunder","red",1,False)[0],"Forced")
        self.assertEqual(override_review_category("Best","green",25,True)[0],"Book")
        white_loss=normalized_move_loss(0,-600,chess.WHITE)
        black_loss=normalized_move_loss(0,600,chess.BLACK)
        self.assertEqual(white_loss[0],black_loss[0])
        self.assertAlmostEqual(white_loss[1],black_loss[1],places=6)
        self.assertEqual(classify(white_loss[0],white_loss[1])[0],"Blunder")
        white_mate_delay=normalized_move_loss(100000,100000,chess.WHITE,3,8)
        black_mate_delay=normalized_move_loss(-100000,-100000,chess.BLACK,-3,-8)
        self.assertEqual(white_mate_delay,black_mate_delay)
        self.assertGreater(white_mate_delay[0],0)

    def test_endgame_material_swing_is_classified_from_mover_perspective(self):
        board=position([
            (chess.WHITE,chess.KING,"e4"),(chess.WHITE,chess.PAWN,"e5"),
            (chess.BLACK,chess.KING,"e8"),(chess.BLACK,chess.PAWN,"d6"),
        ])
        board.fullmove_number=30
        self.assertEqual(game_phase(board),"Endgame")
        loss,ep_loss=normalized_move_loss(80,-520,chess.WHITE)
        self.assertEqual(classify(loss,ep_loss)[0],"Blunder")

    def test_unconfirmed_tactical_cause_is_reported_as_evaluation_only(self):
        text=generate_review_commentary("Blunder","h4","Qe2",30,-400,430,())
        self.assertIn("did not confirm a specific tactical cause",text)

    def test_brilliant_commentary_requires_and_describes_confirmed_sacrifice(self):
        text=generate_review_commentary("Brilliant","Rxa2","Rxa2",40,35,0,(
            ReviewEvent("sound_sacrifice","Your rook sacrifice is engine-approved: the line wins material while keeping expected score within 2 percentage points.","positive"),))
        self.assertIn("Brilliant",text)
        self.assertIn("rook sacrifice",text)
        self.assertIn("within 2 percentage points",text)
        self.assertNotIn("at least as high",text)

    def test_unverified_brilliant_label_is_caveated(self):
        text=generate_review_commentary("Brilliant","Nf3","Nf3",20,20,0,())
        self.assertIn("did not confirm a concrete sacrifice",text)
        self.assertNotIn("Brilliant:",text)

    def test_best_move_commentary_connects_tactic_to_engine_choice(self):
        text=generate_review_commentary("Best","Qh7#","Qh7#",120,100,0,(
            ReviewEvent("checkmate","Qh7# checkmates.","positive"),))
        self.assertIn("leading line",text)
        self.assertIn("checkmates",text)

    def test_game_coach_summary_handles_recurring_lesson_and_names_a_move(self):
        event=ReviewEvent("hanging_piece","Your knight is left undefended.","critical")
        items=[SimpleNamespace(side="White",events=(event,),verdict="Blunder",san="Nf3",ply=5,expected_loss=.2),
               SimpleNamespace(side="White",events=(event,),verdict="Mistake",san="Bc4",ply=9,expected_loss=.1)]
        text=game_coach_summary(items,chess.WHITE)
        self.assertIn("undefended pieces",text)
        self.assertIn("White Nf3",text)
        self.assertIn("Your knight is left undefended",text)
        self.assertIn("scan for opponent captures",text)

    def test_game_coach_summary_uses_the_most_costly_concrete_example(self):
        early=ReviewEvent("hanging_piece","Your bishop on c4 is undefended.","critical")
        costly=ReviewEvent("hanging_piece","Your queen on d5 is left en prise.","critical")
        items=[SimpleNamespace(side="White",events=(early,),verdict="Inaccuracy",san="Bc4",ply=3,expected_loss=.02),
               SimpleNamespace(side="White",events=(costly,),verdict="Blunder",san="Qd5",ply=21,expected_loss=.37)]
        text=game_coach_summary(items,chess.WHITE)
        self.assertIn("White Qd5 (move 11)",text)
        self.assertIn("queen on d5 is left en prise",text)
        self.assertNotIn("White Bc4",text)

    def test_retry_accepts_only_engine_lines_with_near_equal_expected_score(self):
        board=chess.Board()
        lines=[{"pv":[board.parse_san("e4")],"score":chess.engine.PovScore(chess.engine.Cp(30),chess.WHITE)},
               {"pv":[board.parse_san("d4")],"score":chess.engine.PovScore(chess.engine.Cp(25),chess.WHITE)},
               {"pv":[board.parse_san("h4")],"score":chess.engine.PovScore(chess.engine.Cp(-300),chess.WHITE)}]
        accepted=near_best_moves(board,lines,chess.WHITE)
        self.assertIn(board.parse_san("e4"),accepted)
        self.assertIn(board.parse_san("d4"),accepted)
        self.assertNotIn(board.parse_san("h4"),accepted)

    def test_coach_strength_counts_tactical_moves_once(self):
        events=(ReviewEvent("fork","The move forks two pieces.","positive"),
                ReviewEvent("discovered_check","The move uncovers check.","positive"))
        move=SimpleNamespace(side="White",events=events,verdict="Best",san="Nd5",ply=12,expected_loss=0)
        text=game_coach_summary([move],chess.WHITE)
        self.assertIn("tactical idea in one move",text)
        self.assertIn("6. Nd5: The move forks two pieces.",text)

    def test_read_aloud_text_follows_move_and_includes_actionable_details(self):
        event=ReviewEvent("missed_tactic","The knight could fork the king and queen.","critical",
                          "Which two valuable targets are hit?")
        item=SimpleNamespace(side="Black",ply=18,move_number=9,san="Qh4",verdict="Miss",
                             explanation="Miss: The knight fork was available.",best_san="Nd5",
                             best_reason="Nd5 wins the queen.",alternative_sans=("Nc7+",),
                             opponent_response="Qxf2+",events=(event,))
        spoken=review_spoken_text(item)
        self.assertTrue(spoken.startswith("9... Qh4, miss."))
        self.assertIn("Nd5 wins the queen",spoken)
        self.assertIn("Other near-equal choices: Nc7+",spoken)
        self.assertIn("Strongest reply: Qxf2+",spoken)
        self.assertIn("Think it through: Which two valuable targets are hit?",spoken)

    def test_reflection_prompt_uses_the_most_important_verified_event(self):
        events=(ReviewEvent("fork","The knight forks the king and queen.","positive"),
                ReviewEvent("lost_defender","Moving the bishop leaves the queen undefended.","critical"))
        self.assertEqual(review_reflection_prompt(events),
                         "Before moving a defender, which other piece still depends on it?")
        self.assertIsNone(review_reflection_prompt((ReviewEvent("unverified","No concrete change found.","info"),)))

    def test_practice_hint_comes_from_the_engine_move_and_does_not_reveal_it(self):
        fork_board=position([
            (chess.WHITE,chess.KING,"h1"),(chess.WHITE,chess.KNIGHT,"c3"),
            (chess.BLACK,chess.KING,"e7"),(chess.BLACK,chess.QUEEN,"f6"),
            (chess.BLACK,chess.PAWN,"d5"),
        ])
        fork=fork_board.parse_san("Nxd5")
        hint=review_practice_hint(fork_board,fork,(fork,))
        self.assertIn("two valuable targets",hint)
        self.assertNotIn("Nxd5",hint)
        opening=chess.Board();development=opening.parse_san("Nf3")
        hint=review_practice_hint(opening,development,(development,))
        self.assertIn("control of the central squares",hint)

    def test_coach_summary_calls_out_unexplained_inaccuracy(self):
        move=SimpleNamespace(side="White",events=(),verdict="Inaccuracy",san="Re1",ply=19,expected_loss=.07)
        text=game_coach_summary([move],chess.WHITE)
        self.assertIn("largest unexplained evaluation swing was Re1",text)

    def test_positive_pin_is_not_reported_as_the_players_weakness(self):
        pin=ReviewEvent("new_pin","Your bishop pins the enemy knight.","positive")
        move=SimpleNamespace(side="White",events=(pin,),verdict="Best",san="Bb5",ply=3,expected_loss=0)
        text=game_coach_summary([move],chess.WHITE)
        self.assertNotIn("pieces left pinned",text)
        self.assertIn("tactical highlight",text)

    def test_player_overview_uses_the_users_moves_not_opponents(self):
        items=[SimpleNamespace(side="Black",san="Nf6",verdict="Mistake",accuracy=80,expected_loss=.12),
               SimpleNamespace(side="White",san="Qh5",verdict="Blunder",accuracy=10,expected_loss=.65)]
        text=player_review_overview(items,chess.BLACK)
        self.assertIn("Your biggest swing was your Nf6",text)
        self.assertNotIn("Qh5",text)

    def test_my_moves_filter_works_before_full_review_finishes(self):
        items=[SimpleNamespace(side="White",verdict="Best",book_status="Out of book")]
        self.assertEqual(review_filter_indices(items,5,"My moves",chess.WHITE,chess.WHITE),[0,2,4])
        self.assertEqual(review_filter_indices(items,5,"My moves",chess.BLACK,chess.WHITE),[1,3])

    def test_review_selection_ignores_cleared_or_out_of_range_rows(self):
        self.assertEqual(review_row_ply([2,5,9],1),5)
        self.assertIsNone(review_row_ply([2,5,9],-1))
        self.assertIsNone(review_row_ply([2,5,9],3))

    def test_left_and_right_keys_navigate_the_guided_review(self):
        class KeyTarget:
            reviewing=True
            history=[object()]
            def __init__(self):self.steps=[]
            def navigate(self,delta):self.steps.append(delta)
        target=KeyTarget()
        for key in (Qt.Key.Key_Left,Qt.Key.Key_Right):
            event=QKeyEvent(QEvent.Type.KeyPress,key,Qt.KeyboardModifier.NoModifier)
            ChessWindow.keyPressEvent(target,event)
            self.assertTrue(event.isAccepted())
        self.assertEqual(target.steps,[-1,1])

    def test_default_retry_session_uses_only_the_players_own_errors(self):
        items=[SimpleNamespace(side="White",verdict="Blunder",san="Qh5"),
               SimpleNamespace(side="Black",verdict="Mistake",san="Nf6"),
               SimpleNamespace(side="White",verdict="Best",san="Nc3")]
        self.assertEqual([item.san for item in review_practice_moves(items,chess.WHITE)],["Qh5"])
        self.assertEqual([item.san for item in review_practice_moves(items,chess.BLACK)],["Nf6"])

    def test_profile_recurring_themes_count_distinct_games_not_events(self):
        stats=[{"error_themes":{"Loose piece":4,"Missed mate":1}},
               {"error_themes":{"Loose piece":1}},
               {"error_themes":{"Missed mate":3,"King safety weakened":1}}]
        self.assertEqual(recurring_review_themes(stats),[("Loose piece",2),("Missed mate",2)])
        self.assertEqual(recurring_review_themes(stats[:1]),[])

    def test_review_impact_shows_classification_metric_and_cp_context(self):
        self.assertEqual(format_review_impact(.083,145),"8.3 pp expected score lost · 145 cp")

    def test_summary_key_moments_prioritize_impact_but_keep_game_order(self):
        items=[SimpleNamespace(verdict="Mistake",expected_loss=.05,ply=ply,san=f"m{ply}") for ply in range(1,10)]
        items[-1].expected_loss=.40
        selected=rank_review_moments(items,4)
        plies=[item.ply for item in selected]
        self.assertEqual(len(plies),4)
        self.assertIn(9,plies)
        self.assertEqual(plies,sorted(plies))
        self.assertEqual(set(review_moment_tags(items,4)),set(plies))

    def test_annotated_pgn_keeps_played_line_main_and_adds_engine_variation(self):
        start=chess.Board();e4=start.parse_san("e4");d4=start.parse_san("d4")
        board=start.copy();board.push(e4);e5=board.parse_san("e5");played=[e4,e5]
        line=start.copy();line.push(d4);d5=line.parse_san("d5")
        item=SimpleNamespace(best_move=d4,best_san="d4",san="e4",verdict="Inaccuracy",
                             explanation="Missed the central pawn push.",pv="d4 d5",best_pv_moves=(d4,d5),ply=1)
        game=chess.pgn.Game.from_board(start)
        annotate_review_pgn(game,played,[item])
        self.assertEqual(game.variations[0].san(),"e4")
        self.assertEqual(game.variations[1].san(),"d4")
        self.assertEqual(game.variations[1].variations[0].san(),"d5")
        self.assertEqual(game.variations[0].comment.count("Inaccuracy:"),1)

    def test_summary_key_moment_button_opens_selected_ply(self):
        app=QApplication.instance() or QApplication([])
        moves=[SimpleNamespace(side="White",accuracy=91,verdict="Blunder",book_status="Out of book",
                               ply=17,san="Qh5",expected_loss=.25,events=(),explanation="Test move")]
        dialog=ReviewSummaryDialog(moves,"Test opening","1-0",chess.WHITE)
        button=next(button for button in dialog.findChildren(QPushButton) if button.text()=="9. Qh5 · Blunder · 25.0 pp expected score lost")
        button.click()
        self.assertEqual(dialog.selected_ply,17)
        self.assertEqual(dialog.result(),dialog.DialogCode.Accepted)
        dialog.deleteLater();app.processEvents()

    def test_current_position_practice_can_include_a_non_error_move(self):
        app=QApplication.instance() or QApplication([])
        board=chess.Board();first=board.parse_san("e4");board.push(first);second=board.parse_san("e5")
        items=[SimpleNamespace(ply=1,side="White",san="e4",verdict="Best"),
               SimpleNamespace(ply=2,side="Black",san="e5",verdict="Good")]
        dialog=MistakePracticeDialog(chess.Board(),items,[first,second],start_ply=1,include_all=True)
        self.assertEqual(len(dialog.items),2)
        self.assertIn("top move or an equally strong choice",dialog.prompt.text())
        dialog.deleteLater();app.processEvents()

    def test_position_practice_accepts_and_labels_a_sound_alternative(self):
        app=QApplication.instance() or QApplication([])
        board=chess.Board();best=board.parse_san("e4");alternative=board.parse_san("d4")
        item=SimpleNamespace(ply=1,side="White",san="e4",verdict="Best",best_move=best,
                             acceptable_moves=(best,alternative),best_san="e4",theme="Center",
                             explanation="Both central moves are strong.",pv="e4 e5")
        dialog=MistakePracticeDialog(board,[item],[],start_ply=1,include_all=True)
        dialog.on_square(chess.parse_square("d2"));dialog.on_square(chess.parse_square("d4"))
        self.assertTrue(dialog.revealed)
        self.assertEqual(dialog.status.text(),"Strong choice. The engine rates this move effectively equal to its top line.")
        dialog.deleteLater();app.processEvents()

    def test_retry_dialog_shows_async_engine_feedback_after_wrong_attempt(self):
        app=QApplication.instance() or QApplication([])
        start=chess.Board();played=start.parse_san("d4");best=start.parse_san("e4")
        item=SimpleNamespace(ply=1,side="White",san="d4",verdict="Mistake",best_move=best,
                             acceptable_moves=(best,),confidence="Standard analysis")
        dialog=MistakePracticeDialog(start,[item],[played],start_ply=1,include_all=True)
        dialog.analysis_requested.connect(lambda request:dialog.apply_engine_feedback(
            {"token":request[0],"feedback":"Mistake: Black can meet this with ...d5."}))
        dialog.on_square(chess.parse_square("d2"));dialog.on_square(chess.parse_square("d4"))
        self.assertIn("meet this with ...d5",dialog.status.text())
        self.assertTrue(dialog.board.isEnabled())
        self.assertEqual(dialog.board.last_move,played)
        dialog.deleteLater();app.processEvents()


if __name__=="__main__":
    unittest.main()
