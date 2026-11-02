"""Stockfish process lifecycle and chess review analysis worker."""
from __future__ import annotations

import hashlib
import random
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import chess
import chess.engine
import chess.polyglot
import chess.pgn
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from .config import MAX_ELO, MIN_ELO, MIN_TARGET_ELO, STOCKFISH_PATH
from .openings import position_key
from .review_logic import *

class EngineWorker(QObject):
    ready = pyqtSignal(dict)
    failed = pyqtSignal(str)
    bot_move = pyqtSignal(object)
    best_move = pyqtSignal(object)
    position_eval = pyqtSignal(int)
    review_progress = pyqtSignal(int, int, int, object)
    review_done = pyqtSignal(int, object, bool)
    practice_analysis_done = pyqtSignal(object)
    explore_done = pyqtSignal(object)
    position_analysis_done = pyqtSignal(object)
    puzzle_progress = pyqtSignal(int, int, int)
    puzzle_done = pyqtSignal(int, object, str)
    operation_error = pyqtSignal(str)
    syzygy_status = pyqtSignal(str, str, bool)

    def __init__(self,syzygy_path=""):
        super().__init__()
        self.engine: Optional[chess.engine.SimpleEngine] = None
        self.option_names = set()
        self.syzygy_path=syzygy_path
        self.syzygy_active=False
        self._elo = MIN_ELO
        self._uci_elo_min = MIN_ELO
        self._uci_elo_max = MAX_ELO
        self._multipv_max = 8
        self.review_cache=OrderedDict()
        self.review_cache_limit=384
        self.stop_event = threading.Event()
        self.review_cancel_event = threading.Event()

    @pyqtSlot()
    def start_engine(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH, timeout=10)
            self.option_names = set(self.engine.options)
            if "UCI_Elo" in self.option_names:
                option=self.engine.options["UCI_Elo"]
                self._uci_elo_min=max(MIN_ELO,int(option.min or MIN_ELO))
                self._uci_elo_max=min(MAX_ELO,int(option.max or MAX_ELO))
            if "MultiPV" in self.option_names:
                self._multipv_max=max(1,min(8,int(self.engine.options["MultiPV"].max or 8)))
            options = {}
            if "UCI_LimitStrength" in self.option_names: options["UCI_LimitStrength"] = True
            if "UCI_Elo" in self.option_names: options["UCI_Elo"] = self._uci_elo_min
            if "Skill Level" in self.option_names: options["Skill Level"] = 0
            if options: self.engine.configure(options)
            syzygy_error=""
            if self.syzygy_path:
                if "SyzygyPath" not in self.option_names:
                    syzygy_error="This Stockfish build does not support Syzygy tablebases."
                elif not Path(self.syzygy_path).is_dir():
                    syzygy_error=f"Tablebase folder not found: {self.syzygy_path}"
                else:
                    try:self._apply_syzygy_path(self.syzygy_path)
                    except Exception as exc:syzygy_error=f"Could not configure Syzygy tablebases: {exc}"
            self.ready.emit({"options": self.option_names,"syzygy_active":self.syzygy_active,
                             "syzygy_error":syzygy_error})
        except Exception as exc:
            self.failed.emit(f"Could not start Stockfish at {STOCKFISH_PATH}: {exc}")

    def _apply_syzygy_path(self,path):
        if "SyzygyPath" not in self.option_names:
            raise RuntimeError("This Stockfish build does not support Syzygy tablebases")
        if path and (not Path(path).is_dir()
                     or (not any(Path(path).glob("*.rtbw"))
                         and not any(Path(path).glob("*.rtbz")))):
            raise RuntimeError(f"No .rtbw or .rtbz Syzygy files found in {path}")
        options={"SyzygyPath":path}
        if "SyzygyProbeLimit" in self.option_names:options["SyzygyProbeLimit"]=7
        if "Syzygy50MoveRule" in self.option_names:options["Syzygy50MoveRule"]=True
        self.engine.configure(options)
        self.syzygy_path=path;self.syzygy_active=bool(path);self.review_cache.clear()

    @pyqtSlot(str)
    def configure_syzygy(self,path):
        try:
            self._apply_syzygy_path(path)
            self.syzygy_status.emit(path,"",self.syzygy_active)
        except Exception as exc:
            self.syzygy_status.emit(path,str(exc),False)

    @pyqtSlot(int)
    def set_elo(self, value: int):
        self._elo = max(MIN_TARGET_ELO, min(MAX_ELO, int(value)))
        try:
            if self.engine:
                options={}
                if "UCI_Elo" in self.option_names:
                    options["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
                if "Skill Level" in self.option_names:
                    if self._elo < MIN_ELO:
                        skill=0
                    else:
                        skill=round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                    skill_option=self.engine.options["Skill Level"]
                    options["Skill Level"]=max(int(skill_option.min or 0),min(int(skill_option.max or 20),skill))
                if options:self.engine.configure(options)
        except Exception as exc: self.operation_error.emit(str(exc))

    @pyqtSlot(object)
    def play_bot(self, task):
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            version, board = task
            duration = .35 if self._elo < 2000 else .65
            if self._elo < MIN_ELO:
                softness=(MIN_ELO-self._elo)/(MIN_ELO-MIN_TARGET_ELO)
                duration=max(.08,.14+.16*(1-softness))
                multipv_count=min(self._multipv_max,2+round(softness*6))
                move=None
                if "MultiPV" in self.option_names:
                    candidates=self.engine.analyse(board,chess.engine.Limit(time=duration),multipv=multipv_count)
                    if isinstance(candidates,dict):candidates=[candidates]
                    moves=[]
                    for candidate in candidates:
                        pv=candidate.get("pv",[])
                        if pv and pv[0] in board.legal_moves and pv[0] not in moves:moves.append(pv[0])
                    non_best_chance=.10+.78*softness
                    if moves and len(moves)>1 and random.random()<non_best_chance:
                        weaker=moves[1:]
                        weights=[1/(index+1)**1.35 for index in range(len(weaker))]
                        move=random.choices(weaker,weights=weights,k=1)[0]
                    elif moves:
                        move=moves[0]
                if move is None:
                    move=self.engine.play(board,chess.engine.Limit(time=duration)).move
                    if "MultiPV" not in self.option_names and random.random()<.03+.16*softness:
                        legal=list(board.legal_moves)
                        if legal:move=random.choice(legal)
            else:
                move=self.engine.play(board, chess.engine.Limit(time=duration)).move
            self.bot_move.emit((version, board.fen(), move))
        except Exception as exc: self.operation_error.emit(f"Bot move failed: {exc}")

    @pyqtSlot(object)
    def find_best(self, board: chess.Board):
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            info = self.engine.analyse(board, chess.engine.Limit(time=.8))
            pv = info.get("pv", [])
            self.best_move.emit(pv[0] if pv else None)
        except Exception as exc: self.operation_error.emit(f"Best move failed: {exc}")

    @pyqtSlot(object)
    def evaluate_position(self, board: chess.Board):
        try:
            if self.engine:
                info = self.engine.analyse(board, chess.engine.Limit(time=.12))
                self.position_eval.emit(eval_cp(info["score"]))
        except Exception as exc: self.operation_error.emit(f"Position evaluation failed: {exc}")

    @pyqtSlot(object)
    def review_game(self, task):
        review_restore={}
        def restore_review_strength():
            if review_restore and self.engine:
                options=review_restore.copy();review_restore.clear()
                try:self.engine.configure(options)
                except Exception as exc:self.operation_error.emit(f"Could not restore opponent strength after review: {exc}")
        try:
            if not self.engine: raise RuntimeError("Stockfish has not started")
            token,start_board,moves,book_path,budget=task
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:
                full_strength["UCI_LimitStrength"]=False;review_restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:
                review_restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "Skill Level" in self.option_names:
                skill=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                option=self.engine.options["Skill Level"]
                review_restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill))
                full_strength["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),20))
            if full_strength:self.engine.configure(full_strength)
            board = start_board.copy(stack=False)
            results = []
            total = len(moves)
            reader=chess.polyglot.open_reader(book_path) if book_path else None
            def analyze(position,multipv=1):
                key=(position.fen(),tuple(move.uci() for move in position.move_stack),round(float(budget),3),
                     multipv,"full-strength",normalized_syzygy_path(self.syzygy_path) if self.syzygy_active else "")
                if key in self.review_cache:
                    self.review_cache.move_to_end(key)
                    return self.review_cache[key]
                kwargs={"multipv":multipv} if multipv>1 and "MultiPV" in self.option_names else {}
                info=self.engine.analyse(position,chess.engine.Limit(time=budget),**kwargs)
                self.review_cache[key]=info
                while len(self.review_cache)>self.review_cache_limit:self.review_cache.popitem(last=False)
                return info

            piece_ids={square:square for square in start_board.piece_map()}
            piece_move_counts={}
            for i, move in enumerate(moves):
                if self.stop_event.is_set():
                    if reader:reader.close()
                    restore_review_strength()
                    return
                if self.review_cancel_event.is_set():
                    if reader:reader.close()
                    restore_review_strength()
                    self.review_done.emit(token,results,True)
                    return
                before = board.copy()
                if move not in board.legal_moves: raise ValueError(f"Illegal move at ply {i + 1}")
                san = board.san(move)
                legal_count=board.legal_moves.count()
                moving_piece=board.piece_at(move.from_square)
                moving_id=piece_ids.get(move.from_square)
                repeated_piece_moves=piece_move_counts.get(moving_id,0)+1 if moving_id is not None else 1
                base_info = analyze(board)
                base_primary=base_info[0] if isinstance(base_info,list) else base_info
                cp_before = eval_cp(base_primary["score"])
                mate_before=base_primary["score"].white().mate()
                pv_moves = base_info.get("pv", [])
                best = pv_moves[0] if pv_moves and pv_moves[0] in board.legal_moves else move
                base_lines=base_info if isinstance(base_info,list) else [base_info]
                best_pv_moves=tuple(pv_moves[:12])
                best_san = board.san(best)
                best_pv_board = board.copy(stack=False);best_pv_sans=[]
                for pv_move in best_pv_moves:
                    if pv_move not in best_pv_board.legal_moves:break
                    best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                mat_before = material_balance(board)
                board.push(move)
                after_info = analyze(board)
                after_primary=after_info[0] if isinstance(after_info,list) else after_info
                cp_after = eval_cp(after_primary["score"])
                mate_after=after_primary["score"].white().mate()
                loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,mate_before,mate_after)
                before_ep=expected_points(cp_before);actual_ep=expected_points(cp_after)
                before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                verdict, color = classify(loss,ep_loss)
                if move==best:verdict,color="Best","#39a982"
                elif verdict=="Best":verdict,color="Excellent","#4baf73"
                delta = material_balance(board) - mat_before
                side_delta=delta if before.turn==chess.WHITE else -delta
                after_lines=after_info if isinstance(after_info,list) else [after_info]
                response_pv=after_lines[0].get("pv",[]) if after_lines else []
                response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                response_san=board.san(response) if response else ""
                response_board=board.copy(stack=False);response_sans=[]
                for response_move in response_pv[:8]:
                    if response_move not in response_board.legal_moves:break
                    response_sans.append(response_board.san(response_move));response_board.push(response_move)

                # Ask for alternatives only on meaningful misses. This keeps
                # MultiPV focused while surfacing candidate ideas on errors.
                candidate_lines=base_lines
                if best!=move and ep_loss>=.05 and "MultiPV" in self.option_names and self._multipv_max>=2:
                    candidate_info=analyze(before,min(3,self._multipv_max))
                    candidate_lines=candidate_info if isinstance(candidate_info,list) else [candidate_info]
                    if candidate_lines and candidate_lines[0].get("pv"):
                        candidate_best=candidate_lines[0]["pv"][0]
                        if candidate_best in before.legal_moves:
                            best=candidate_best;best_san=before.san(best)
                            best_pv_moves=tuple(candidate_lines[0].get("pv",[])[:12])
                            best_pv_board=before.copy(stack=False);best_pv_sans=[]
                            for pv_move in best_pv_moves:
                                if pv_move not in best_pv_board.legal_moves:break
                                best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)

                best_mover_ep=before_mover_ep
                best_cp=cp_before;best_mate_white=mate_before
                if candidate_lines:
                    line_score=candidate_lines[0].get("score")
                    if line_score is not None:
                        candidate_cp=eval_cp(line_score)
                        best_cp=candidate_cp;best_mate_white=line_score.white().mate()
                        candidate_ep=expected_points(candidate_cp)
                        best_mover_ep=candidate_ep if before.turn==chess.WHITE else 1-candidate_ep
                # Grade the played line against the current best line before
                # deciding whether this position needs a deeper confirmation.
                loss,ep_loss=normalized_move_loss(best_cp,cp_after,before.turn,
                                                 best_mate_white,mate_after)
                verdict,color=classify(loss,ep_loss)
                if move==best:verdict,color="Best","#39a982"
                elif verdict=="Best":verdict,color="Excellent","#4baf73"
                critical_confirmed=False;critical_before_info=None;critical_after_info=None
                candidate_mate=(candidate_lines[0]["score"].pov(before.turn).mate()
                                if candidate_lines and candidate_lines[0].get("score") else None)
                actual_mate=after_primary["score"].pov(before.turn).mate() if after_primary.get("score") else None
                needs_critical_confirmation=(ep_loss>=.10 or candidate_mate is not None or actual_mate is not None)
                if budget>=.18 and needs_critical_confirmation:
                    critical_limit=chess.engine.Limit(time=max(.8,min(1.5,budget*2)))
                    critical_kwargs=({"multipv":min(3,self._multipv_max)}
                                     if "MultiPV" in self.option_names and self._multipv_max>=2 else {})
                    try:
                        critical_before_info=self.engine.analyse(before,critical_limit,**critical_kwargs)
                        critical_after_info=self.engine.analyse(board,critical_limit)
                    except Exception:
                        critical_before_info=None;critical_after_info=None
                    if critical_before_info and critical_after_info:
                        critical_lines=(critical_before_info if isinstance(critical_before_info,list)
                                        else [critical_before_info])
                        critical_best_line=next((line for line in critical_lines
                                                 if line.get("pv") and line["pv"][0] in before.legal_moves
                                                 and line.get("score") is not None),None)
                        critical_after_score=critical_after_info.get("score")
                        if critical_best_line and critical_after_score is not None:
                            candidate_lines=critical_lines
                            best=critical_best_line["pv"][0];best_san=before.san(best)
                            best_pv_moves=tuple(critical_best_line["pv"][:12])
                            best_pv_board=before.copy(stack=False);best_pv_sans=[]
                            for pv_move in best_pv_moves:
                                if pv_move not in best_pv_board.legal_moves:break
                                best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                            cp_before=eval_cp(critical_best_line["score"])
                            mate_before=critical_best_line["score"].white().mate()
                            cp_after=eval_cp(critical_after_score)
                            mate_after=critical_after_score.white().mate()
                            before_ep=expected_points(cp_before);actual_ep=expected_points(cp_after)
                            before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                            actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                            best_cp=cp_before;best_mate_white=mate_before;best_mover_ep=before_mover_ep
                            loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,
                                                               mate_before,mate_after)
                            verdict,color=classify(loss,ep_loss)
                            if move==best:verdict,color="Best","#39a982"
                            elif verdict=="Best":verdict,color="Excellent","#4baf73"
                            after_info=critical_after_info;after_primary=critical_after_info
                            after_lines=[after_primary]
                            response_pv=after_primary.get("pv",[])
                            response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                            response_san=board.san(response) if response else ""
                            response_board=board.copy(stack=False);response_sans=[]
                            for response_move in response_pv[:8]:
                                if response_move not in response_board.legal_moves:break
                                response_sans.append(response_board.san(response_move));response_board.push(response_move)
                            critical_confirmed=True
                turnaround_candidate=(ep_loss<=.02 and best_mover_ep<.25
                                      and actual_mover_ep>=.50)
                best_mate_distance=(candidate_lines[0]["score"].pov(before.turn).mate()
                                    if candidate_lines and candidate_lines[0].get("score") else None)
                best_mate=best_mate_distance is not None and best_mate_distance>0
                actual_mate_distance=(after_primary["score"].pov(before.turn).mate()
                                      if after_primary.get("score") else None)
                if board.is_checkmate():actual_mate_distance=0
                # The played line's distance is measured after one ply has
                # already elapsed, unlike the best line's root distance.
                mate_is_delayed=(best_mate_distance is not None and actual_mate_distance is not None
                                 and actual_mate_distance>0
                                 and actual_mate_distance+1>best_mate_distance)
                missed_mate=(best_mate and (actual_mate_distance is None or actual_mate_distance<0
                                             or mate_is_delayed))
                if best!=move and (missed_mate or (best_mover_ep>=.75 and actual_mover_ep<.55
                                                 and best_mover_ep-actual_mover_ep>=.15)):
                    verdict,color="Miss","#e68a47"

                book_entries=list(reader.find_all(before)) if reader else []
                book_entry=next((entry for entry in book_entries if entry.move==move),None)
                book_status=(f"Book move · weight {book_entry.weight}" if book_entry else "Out of book") if reader else "Book not loaded"
                only_good_gap=0.0
                if (move==best and ep_loss<=.02 and budget>=.4 and not critical_confirmed and not book_entry
                        and legal_count>1 and legal_count<=self._multipv_max
                        and "MultiPV" in self.option_names):
                    all_move_lines=analyze(before,legal_count)
                    all_move_lines=all_move_lines if isinstance(all_move_lines,list) else [all_move_lines]
                    if best in near_best_moves(before,all_move_lines,before.turn):
                        candidate_lines=all_move_lines
                    only_good_gap=only_good_move_gap(before,all_move_lines,before.turn,move,
                                                      legal_count,before_mover_ep)
                    if only_good_gap:verdict,color="Great","#45b9d0"
                verdict,color=override_review_category(verdict,color,legal_count,bool(book_entry))

                # A Great turnaround changes the story of the game, so verify
                # the swing with a deeper search before assigning the label.
                turnaround_confirmed=False;turnaround_before_ep=None;turnaround_after_ep=None
                if (turnaround_candidate and verdict not in ("Book","Forced")
                        and not only_good_gap):
                    confirmation_limit=chess.engine.Limit(time=max(1.2,min(3.0,budget*5)))
                    try:
                        turnaround_before_info=self.engine.analyse(before,confirmation_limit)
                        turnaround_after_info=self.engine.analyse(board,confirmation_limit)
                    except Exception:
                        turnaround_before_info=None;turnaround_after_info=None
                    if turnaround_before_info and turnaround_after_info:
                        before_score=turnaround_before_info.get("score")
                        after_score=turnaround_after_info.get("score")
                        if before_score is not None and after_score is not None:
                            before_deep_white_ep=expected_points(eval_cp(before_score))
                            turnaround_before_ep=(before_deep_white_ep if before.turn==chess.WHITE
                                                  else 1-before_deep_white_ep)
                            if board.is_checkmate():turnaround_after_ep=1.0
                            else:
                                after_deep_white_ep=expected_points(eval_cp(after_score))
                                turnaround_after_ep=(after_deep_white_ep if before.turn==chess.WHITE
                                                     else 1-after_deep_white_ep)
                            turnaround_confirmed=(turnaround_before_ep<.25
                                                  and turnaround_after_ep>=.50
                                                  and turnaround_before_ep-turnaround_after_ep<=.02)
                    if turnaround_confirmed:verdict,color="Great","#45b9d0"

                # Use the regular pass to find candidates, then spend a deeper
                # search only on possible brilliancies. Confirm the actual
                # post-sacrifice position as well as the root alternatives.
                justified=is_engine_justified_sacrifice(before,move,best,best_pv_moves,before_mover_ep,actual_mover_ep,best_mate)
                brilliant_gap=0.0
                if (justified and budget>=.4 and not book_entry and legal_count>1
                        and "MultiPV" in self.option_names and self._multipv_max>=2):
                    confirmation_limit=chess.engine.Limit(time=max(2.0,budget))
                    try:
                        brilliant_info=self.engine.analyse(before,confirmation_limit,
                                                           multipv=min(3,self._multipv_max))
                        brilliant_lines=brilliant_info if isinstance(brilliant_info,list) else [brilliant_info]
                        deep_after_info=None if board.is_checkmate() else self.engine.analyse(board,confirmation_limit)
                    except Exception:
                        # A failed confirmation must never create a Brilliant
                        # label or abort an otherwise usable game review.
                        justified=False;brilliant_gap=0.0
                    else:
                        deep_best_line=next((line for line in brilliant_lines
                                             if line.get("pv") and line["pv"][0] in before.legal_moves),None)
                        deep_actual_mover_ep=1.0 if board.is_checkmate() else None
                        if deep_after_info:
                            deep_actual_white_ep=expected_points(eval_cp(deep_after_info["score"]))
                            deep_actual_mover_ep=(deep_actual_white_ep if before.turn==chess.WHITE
                                                  else 1-deep_actual_white_ep)
                        if deep_best_line and deep_best_line.get("score") and deep_actual_mover_ep is not None:
                            deep_best_move=deep_best_line["pv"][0]
                            deep_best_pv=tuple(deep_best_line["pv"][:12])
                            deep_white_ep=expected_points(eval_cp(deep_best_line["score"]))
                            deep_best_mover_ep=deep_white_ep if before.turn==chess.WHITE else 1-deep_white_ep
                            deep_mate=engine_line_forces_mate(deep_best_line["score"],before.turn)
                            justified=is_engine_justified_sacrifice(before,move,deep_best_move,deep_best_pv,
                                                                     deep_best_mover_ep,deep_actual_mover_ep,deep_mate)
                            if justified:
                                brilliant_gap=brilliant_choice_gap(
                                    before,brilliant_lines,before.turn,move,
                                    minimum_gap=BRILLIANT_MIN_UNIQUENESS_GAP)
                        else:justified=False
                if can_label_brilliant(justified,budget,bool(book_entry),legal_count,
                                       brilliant_gap>=BRILLIANT_MIN_UNIQUENESS_GAP):
                    verdict,color="Brilliant","#b68ae0"
                    # Keep every surface of a Brilliant review on the same
                    # deeper search that earned the label. Otherwise the badge
                    # could cite a confirmed line while the score, best move,
                    # and replay line still came from the shallower pass.
                    candidate_lines=brilliant_lines
                    best=move;best_san=san;best_pv_moves=deep_best_pv
                    best_pv_board=before.copy(stack=False);best_pv_sans=[]
                    for pv_move in best_pv_moves:
                        if pv_move not in best_pv_board.legal_moves:break
                        best_pv_sans.append(best_pv_board.san(pv_move));best_pv_board.push(pv_move)
                    cp_before=eval_cp(deep_best_line["score"])
                    mate_before=deep_best_line["score"].white().mate()
                    if deep_after_info:
                        cp_after=eval_cp(deep_after_info["score"])
                        mate_after=deep_after_info["score"].white().mate()
                        actual_ep=expected_points(cp_after)
                    else:
                        # A checkmating sacrifice has no post-move engine
                        # search; record the terminal result directly.
                        cp_after=100_000 if before.turn==chess.WHITE else -100_000
                        mate_after=0
                        actual_ep=1.0 if before.turn==chess.WHITE else 0.0
                    before_ep=expected_points(cp_before)
                    before_mover_ep=before_ep if before.turn==chess.WHITE else 1-before_ep
                    actual_mover_ep=actual_ep if before.turn==chess.WHITE else 1-actual_ep
                    best_cp=cp_before;best_mate_white=mate_before
                    best_mover_ep=before_mover_ep
                    loss,ep_loss=normalized_move_loss(cp_before,cp_after,before.turn,
                                                       mate_before,mate_after)
                    best_mate_distance=deep_best_line["score"].pov(before.turn).mate()
                    best_mate=best_mate_distance is not None and best_mate_distance>0
                    actual_mate_distance=(deep_after_info["score"].pov(before.turn).mate()
                                          if deep_after_info else 0)
                    if board.is_checkmate():actual_mate_distance=0
                    mate_is_delayed=(best_mate_distance is not None and actual_mate_distance is not None
                                     and actual_mate_distance>0
                                     and actual_mate_distance+1>best_mate_distance)
                    missed_mate=(best_mate and (actual_mate_distance is None or actual_mate_distance<0
                                                 or mate_is_delayed))
                    after_lines=[deep_after_info] if deep_after_info else []
                    response_pv=deep_after_info.get("pv",[]) if deep_after_info else []
                    response=response_pv[0] if response_pv and response_pv[0] in board.legal_moves else None
                    response_san=board.san(response) if response else ""
                    response_board=board.copy(stack=False);response_sans=[]
                    for response_move in response_pv[:8]:
                        if response_move not in response_board.legal_moves:break
                        response_sans.append(response_board.san(response_move));response_board.push(response_move)

                if best!=move and ep_loss>=.05 and verdict not in ("Miss","Book","Forced") and "MultiPV" in self.option_names:
                    # The multi-PV is the same root position, so a large gap
                    # between best and played alternatives strengthens a miss label.
                    scores=[]
                    for line in candidate_lines:
                        line_pv=line.get("pv",[]);line_score=line.get("score")
                        if line_pv and line_score and line_pv[0]==move:
                            score_cp=eval_cp(line_score)
                            scores.append(score_cp if before.turn==chess.WHITE else -score_cp)
                    if scores and best_mover_ep-actual_mover_ep>=.30 and before_mover_ep-best_mover_ep>=.15:
                        verdict,color="Miss","#e68a47"

                side="White" if before.turn==chess.WHITE else "Black"
                events=analyze_review_events(before,move,board,best,response,best_pv_moves,repeated_piece_moves,
                                             tuple(response_pv[:8]),explain_best_difference=ep_loss>=.05 or missed_mate)
                if turnaround_confirmed:
                    events+=(ReviewEvent("turnaround",
                                         f"A deeper engine search confirms this move shifts {side} from "
                                         f"{turnaround_before_ep*100:.0f}% to {turnaround_after_ep*100:.0f}% expected score.",
                                         "positive",
                                         "Which feature of this position lets this move turn a likely loss into a drawable game?",
                                         (move.from_square,move.to_square)),)
                if only_good_gap:
                    events+=(ReviewEvent("only_good_move",
                                         f"{san} was the only legal move that kept at least an equal position; "
                                         f"the next-best legal move loses {only_good_gap*100:.1f} percentage points of expected score.",
                                         "positive",
                                         "Which part of this position makes the other legal moves fail to hold equality?",
                                         (move.from_square,move.to_square)),)
                if missed_mate and best!=move:
                    if actual_mate_distance is not None and actual_mate_distance>0:
                        mate_text=(f"The best move {best_san} forces mate after {best_mate_distance} plies; "
                                   f"{san} still forces mate, but only after {actual_mate_distance+1} plies from here.")
                        mate_prompt=(f"Both moves retain mate. What forcing line after {best_san} ends it sooner than {san}?")
                    else:
                        mate_text=f"The best move {best_san} forces mate after {best_mate_distance} plies; {san} lets the forced mate escape."
                        mate_prompt=f"What sequence of checks makes {best_san} a forced mate after {best_mate_distance} plies?"
                    if any(event.kind=="missed_mate" for event in events):
                        events=tuple(ReviewEvent(event.kind,mate_text,event.severity,mate_prompt,event.focus_squares)
                                     if event.kind=="missed_mate" else event for event in events)
                    else:events+=(ReviewEvent("missed_mate",mate_text,"critical",mate_prompt),)
                elif best_mate and best!=move:
                    events=tuple(event for event in events if event.kind!="missed_mate")
                if verdict=="Brilliant":
                    sacrifice_square=chess.square_name(move.to_square)
                    victim,victim_square=captured_piece_details(before,move)
                    victim_location=(f" on {chess.square_name(victim_square)}" if victim_square is not None else "")
                    offer=(f"takes the {piece_label(victim)}{victim_location} and offers the {piece_label(moving_piece)} on {sacrifice_square}"
                           if victim else f"offers the {piece_label(moving_piece)} on {sacrifice_square}")
                    deep_line_board=before.copy(stack=False);deep_line_sans=[]
                    for line_move in deep_best_pv[:8]:
                        if line_move not in deep_line_board.legal_moves:break
                        deep_line_sans.append(deep_line_board.san(line_move));deep_line_board.push(line_move)
                    line_text=(f" The confirmed line is {' '.join(deep_line_sans)}."
                               if deep_line_sans else "")
                    events+=(ReviewEvent("sound_sacrifice",
                                         f"Your {piece_label(moving_piece)} {san} {offer}; the deeper engine line keeps your expected score within 2 percentage points of, or above, its pre-move level, and MultiPV puts it {brilliant_gap*100:.1f} percentage points ahead of the next-best alternative.{line_text}",
                                         "positive"),)
                actual_themes=tactical_themes(before,move)
                theme=", ".join(sorted(actual_themes))
                best_themes=tactical_themes(before,best) if best!=move else set()
                side_name="White" if before.turn==chess.WHITE else "Black"
                best_reason=(f"The engine estimates {best_mover_ep*100:.0f}% expected score for {side_name} after {best_san}, "
                             f"versus {actual_mover_ep*100:.0f}% after {san}. Line evaluations: "
                             f"{eval_text(best_cp)} after {best_san}, {eval_text(cp_after)} after {san}.")
                if best_themes:
                    best_reason+=" The alternative also creates "+", ".join(sorted(best_themes))+"."
                note=generate_review_commentary(verdict,san,best_san,cp_before,cp_after,loss,events,
                                                response_san,
                                                best_reason," ".join(best_pv_sans),ep_loss,i+1)
                confidence=("Deep confirmation" if critical_confirmed or turnaround_confirmed or verdict=="Brilliant"
                           else "Quick estimate" if budget<.18
                           else "Standard analysis" if budget<.8 else "Thorough analysis")
                if turnaround_confirmed:
                    depth_infos=(turnaround_before_info,turnaround_after_info)
                elif verdict=="Brilliant" and deep_best_line:
                    depth_infos=(deep_best_line,deep_after_info)
                elif critical_confirmed:
                    depth_infos=(critical_best_line,critical_after_info)
                else:
                    depth_infos=(base_primary,after_primary)
                reported_depths=[int(info["depth"]) for info in depth_infos
                                if isinstance(info,dict) and info.get("depth") is not None]
                if reported_depths:
                    confidence+=f" · depth {'/'.join(map(str,reported_depths))}"
                if self.syzygy_active and any(int(info.get("tbhits",0) or 0)>0
                                              for info in depth_infos if isinstance(info,dict)):
                    confidence+=" · Syzygy tablebase hit"
                item = ReviewMove(i + 1, san, best_san, verdict, color, loss, cp_after,
                                  " ".join(best_pv_sans), note, side, book_status,
                                  int(book_entry.weight) if book_entry else 0,
                                  game_phase(before), move_accuracy(loss,ep_loss), best,ep_loss,confidence,theme,
                                  cp_before,response_san,
                                  " ".join(response_sans),best_pv_moves,events,best_reason,mate_before,mate_after,response,
                                  near_best_moves(before,candidate_lines,before.turn),
                                  tuple(before.san(candidate) for candidate in near_best_moves(before,candidate_lines,before.turn) if candidate!=best),
                                  before.fullmove_number,tuple(response_pv[:8]))
                results.append(item)
                if moving_id is not None:
                    piece_move_counts[moving_id]=repeated_piece_moves
                    piece_ids.pop(move.from_square,None)
                    capture_square=move.to_square
                    if before.is_en_passant(move):capture_square=move.to_square-8 if before.turn==chess.WHITE else move.to_square+8
                    piece_ids.pop(capture_square,None)
                    if before.is_castling(move):
                        rank=0 if before.turn==chess.WHITE else 7
                        rook_from=chess.square(7 if move.to_square>move.from_square else 0,rank)
                        rook_to=chess.square(5 if move.to_square>move.from_square else 3,rank)
                        rook_id=piece_ids.pop(rook_from,None)
                        if rook_id is not None:piece_ids[rook_to]=rook_id
                    piece_ids[move.to_square]=moving_id
                self.review_progress.emit(token, i + 1, total, item)
            if reader:reader.close()
            restore_review_strength()
            self.review_done.emit(token, results, False)
        except Exception as exc:
            restore_review_strength()
            if "reader" in locals() and reader:
                try:reader.close()
                except Exception:pass
            if "token" in locals():self.review_done.emit(token,locals().get("results",[]),False)
            self.operation_error.emit(f"Game review failed: {exc}")

    @pyqtSlot(object)
    def analyze_practice_attempt(self,task):
        token,before,attempt,best_move,budget=task;restore={}
        try:
            if not self.engine:raise RuntimeError("Stockfish is not ready")
            if attempt not in before.legal_moves or best_move not in before.legal_moves:
                raise ValueError("The practice position changed before analysis")
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:
                full_strength["UCI_LimitStrength"]=False;restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:
                option=self.engine.options["UCI_Elo"]
                full_strength["UCI_Elo"]=int(option.max or self._uci_elo_max)
                restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "Skill Level" in self.option_names:
                option=self.engine.options["Skill Level"]
                full_strength["Skill Level"]=int(option.max or 20)
                skill=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill))
            if full_strength:self.engine.configure(full_strength)
            mover=before.turn;sign=1 if mover==chess.WHITE else -1
            best_after=before.copy(stack=False);best_after.push(best_move)
            attempt_after=before.copy(stack=False);attempt_after.push(attempt)
            limit=chess.engine.Limit(time=max(.6,min(1.5,float(budget)*2)))

            def evaluate(position):
                if position.is_checkmate():return None,1.0 if not position.turn==mover else 0.0
                if position.is_stalemate() or position.is_insufficient_material():return None,.5
                info=self.engine.analyse(position,limit)
                score=info["score"];cp=eval_cp(score)
                white_ep=expected_points(cp)
                mover_ep=white_ep if mover==chess.WHITE else 1-white_ep
                return (info,score,cp),mover_ep

            best_data,best_ep=evaluate(best_after)
            attempt_data,attempt_ep=evaluate(attempt_after)
            best_score=best_data[1] if best_data else None
            attempt_info,attempt_score,attempt_cp=attempt_data if attempt_data else (None,None,0)
            best_mate_distance=(0 if best_after.is_checkmate() else
                                best_score.pov(mover).mate() if best_score else None)
            attempt_mate_distance=(0 if attempt_after.is_checkmate() else
                                   attempt_score.pov(mover).mate() if attempt_score else None)
            before_cp=best_data[2] if best_data else (100000 if best_ep>=1 else -100000 if best_ep<=0 else 0)
            cp_loss=max(0,(before_cp-attempt_cp)*sign)
            ep_loss=max(0.0,best_ep-attempt_ep)
            verdict,color=classify(cp_loss,ep_loss)
            missed_mate=bool(best_mate_distance is not None and best_mate_distance>=0
                             and (attempt_mate_distance is None or attempt_mate_distance<0
                                  or attempt_mate_distance>best_mate_distance))
            if missed_mate:verdict,color="Miss","#e68a47"
            acceptable=ep_loss<=NEAR_BEST_EXPECTED_POINTS_TOLERANCE and not missed_mate
            pv=tuple(attempt_info.get("pv",[])) if attempt_info else ()
            response=pv[0] if pv and pv[0] in attempt_after.legal_moves else None
            response_san=attempt_after.san(response) if response else ""
            events=analyze_review_events(before,attempt,attempt_after,best_move,response,(),0,pv)
            if missed_mate:
                best_mate_phrase=("is checkmate" if best_after.is_checkmate()
                                   else f"forces mate after {best_mate_distance} plies from the resulting position")
                if attempt_mate_distance is not None and attempt_mate_distance>0:
                    mate_text=(f"The top move {before.san(best_move)} {best_mate_phrase}; "
                               f"your move still forces mate, but only after {attempt_mate_distance} plies from the resulting position.")
                else:
                    mate_text=f"The top move {before.san(best_move)} {best_mate_phrase}; your move lets the forced mate escape."
                if any(event.kind=="missed_mate" for event in events):
                    events=tuple(ReviewEvent(event.kind,mate_text,event.severity,event.prompt,event.focus_squares)
                                 if event.kind=="missed_mate" else event for event in events)
                else:events+=(ReviewEvent("missed_mate",mate_text,"critical"),)
            elif best_mate_distance is not None and best_mate_distance>0:
                events=tuple(event for event in events if event.kind!="missed_mate")
            concrete=[event.text for event in events if event.severity in ("critical","warning") and event.kind!="opponent_check"]
            mover_name="White" if mover==chess.WHITE else "Black"
            comparison=(f"Engine estimate for {mover_name}: {attempt_ep*100:.0f}% expected score after {before.san(attempt)}, "
                        f"compared with {best_ep*100:.0f}% after {before.san(best_move)}.")
            explanation=(" ".join(concrete[:2])+" "+comparison if concrete else comparison)
            reply_text=f" Strongest reply: {response_san}." if response_san else ""
            pv_board=attempt_after.copy(stack=False);pv_sans=[]
            for line_move in pv[:6]:
                if line_move not in pv_board.legal_moves:break
                pv_sans.append(pv_board.san(line_move));pv_board.push(line_move)
            line_text=" Engine line: "+" ".join(pv_sans) if pv_sans else ""
            feedback=(f"Strong choice: this move is within {NEAR_BEST_EXPECTED_POINTS_TOLERANCE*100:.0f} percentage point of the engine's top line. {explanation}"
                      if acceptable else f"{verdict}: {explanation}")
            result={"token":token,"feedback":f"{feedback}{reply_text}{line_text}","acceptable":acceptable}
        except Exception as exc:
            result={"token":token,"feedback":f"Could not analyze that attempt: {exc or 'Stockfish could not evaluate this position.'}"}
        finally:
            if restore and self.engine:
                try:self.engine.configure(restore)
                except Exception:pass
        self.practice_analysis_done.emit(result)

    @pyqtSlot(object)
    def explore(self,task):
        try:
            if not self.engine:raise RuntimeError("Stockfish has not started")
            board,book_path,count=task
            found={}
            if book_path:
                with chess.polyglot.open_reader(book_path) as reader:
                    for entry in reader.find_all(board):
                        if entry.move in board.legal_moves:
                            found[entry.move]={"move":entry.move,"san":board.san(entry.move),"weight":int(entry.weight),"eval":None,"pv":""}
            multipv=min(max(1,count),self._multipv_max)
            info=self.engine.analyse(board,chess.engine.Limit(time=.45),multipv=multipv) if "MultiPV" in self.option_names else self.engine.analyse(board,chess.engine.Limit(time=.65))
            if isinstance(info,dict):info=[info]
            for line in info:
                pv=line.get("pv",[])
                if not pv or pv[0] not in board.legal_moves:continue
                move=pv[0]
                item=found.setdefault(move,{"move":move,"san":board.san(move),"weight":0,"eval":None,"pv":""})
                item["eval"]=eval_cp(line["score"])
                pv_board=board.copy();sans=[]
                for pv_move in pv[:8]:
                    if pv_move not in pv_board.legal_moves:break
                    sans.append(pv_board.san(pv_move));pv_board.push(pv_move)
                item["pv"]=" ".join(sans)
            rows=list(found.values())
            if book_path:rows.sort(key=lambda x:(x["weight"],abs(x["eval"] or 0)),reverse=True)
            else:rows.sort(key=lambda x:(x["eval"] if board.turn==chess.WHITE else -(x["eval"] or 0)),reverse=True)
            self.explore_done.emit((board.fen(),rows))
        except Exception as exc:self.operation_error.emit(f"Opening explorer failed: {exc}")

    @pyqtSlot(object)
    def analyze_position(self, task):
        token, source_board, budget = task
        fen = source_board.fen()
        restore = {}
        results = {}
        error = ""
        try:
            if not self.engine:
                raise RuntimeError("Stockfish has not started")
            budget = max(.05, min(15.0, float(budget)))
            full_strength = {}
            if "UCI_LimitStrength" in self.option_names:
                full_strength["UCI_LimitStrength"] = False
                restore["UCI_LimitStrength"] = True
            if "UCI_Elo" in self.option_names:
                option = self.engine.options["UCI_Elo"]
                full_strength["UCI_Elo"] = int(option.max or self._uci_elo_max)
                restore["UCI_Elo"] = max(self._uci_elo_min, min(self._uci_elo_max, self._elo))
            if "Skill Level" in self.option_names:
                option = self.engine.options["Skill Level"]
                full_strength["Skill Level"] = int(option.max or 20)
                skill = (0 if self._elo < MIN_ELO else
                         round((self._elo - MIN_ELO) / (MAX_ELO - MIN_ELO) * 20))
                restore["Skill Level"] = max(int(option.min or 0), min(int(option.max or 20), skill))
            if full_strength:
                self.engine.configure(full_strength)

            for color, label in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
                position = source_board.copy(stack=False)
                position.turn = color
                legal_moves = list(position.legal_moves)
                if not legal_moves:
                    results[label] = []
                    continue

                candidate_lines = []
                multipv = min(3, self._multipv_max)
                if "MultiPV" in self.option_names and multipv > 1:
                    analyzed = self.engine.analyse(
                        position, chess.engine.Limit(time=budget), multipv=multipv)
                    candidate_lines.extend(analyzed if isinstance(analyzed, list) else [analyzed])

                seen = {
                    line.get("pv", [None])[0]
                    for line in candidate_lines
                    if line.get("pv") and line.get("pv")[0] in position.legal_moves
                }
                # Stockfish normally supports MultiPV >= 3. If it does not, or
                # returns fewer usable lines than requested, ask for the next
                # best move while excluding candidates already found.
                while len(seen) < min(3, len(legal_moves)):
                    remaining = [move for move in legal_moves if move not in seen]
                    if not remaining:
                        break
                    info = self.engine.analyse(
                        position, chess.engine.Limit(time=budget), root_moves=remaining)
                    pv = info.get("pv", [])
                    if not pv or pv[0] not in remaining:
                        break
                    seen.add(pv[0])
                    candidate_lines.append(info)

                rows = []
                for line in candidate_lines:
                    pv = line.get("pv", [])
                    if not pv or pv[0] not in position.legal_moves:
                        continue
                    move = pv[0]
                    if any(row["move"] == move for row in rows):
                        continue
                    side_score = line["score"].pov(color)
                    mate = side_score.mate()
                    if mate is not None:
                        score_text = (f"+M{mate}" if mate > 0 else
                                      f"−M{abs(mate)}" if mate < 0 else "M0")
                    else:
                        score_text = eval_text(int(side_score.score() or 0))
                    pv_board = position.copy(stack=False)
                    san_line = []
                    for pv_move in pv[:8]:
                        if pv_move not in pv_board.legal_moves:
                            break
                        san_line.append(pv_board.san(pv_move))
                        pv_board.push(pv_move)
                    rows.append({
                        "move": move,
                        "san": position.san(move),
                        "eval": score_text,
                        "pv": " ".join(san_line),
                        "pv_uci": [pv_move.uci() for pv_move in pv[:8]],
                        "score": side_score.score(mate_score=100_000),
                    })
                rows.sort(key=lambda row: row["score"] if row["score"] is not None else -100_001,
                          reverse=True)
                results[label] = rows[:3]
        except Exception as exc:
            error = str(exc) or "Stockfish could not analyze this position."
        finally:
            if restore and self.engine:
                try:
                    self.engine.configure(restore)
                except Exception as exc:
                    self.operation_error.emit(f"Could not restore opponent strength after position analysis: {exc}")
        self.position_analysis_done.emit({
            "token": token,
            "fen": fen,
            "results": results,
            "error": error,
        })

    @pyqtSlot(object)
    def generate_puzzle(self,task):
        token,request=task
        mode=request.get("mode","focus")
        skill=request.get("skill","Any tactic")
        phase=request.get("phase","")
        if mode=="personal":phase=request.get("personal_phase",phase)
        piece_focus=request.get("piece","")
        challenge_mode=bool(request.get("challenge",False))
        source=request.get("source","generated")
        challenge_score=max(0,int(request.get("challenge_score",0)))
        difficulty=max(1,min(10,int(request.get("difficulty",5))))
        mate_in=max(0,min(5,int(request.get("mate_in",0))))
        source_positions=request.get("source_positions",[])
        recent_themes=request.get("recent_themes",[])
        try:
            if not self.engine:raise RuntimeError("Stockfish is not available")
            source_fens=sorted(str(item.get("fen","")) if isinstance(item,dict) else str(item)
                               for item in source_positions)
            recent_fens=set(request.get("recent_fens",[]))
            seed_material="|".join(("offline-chess-puzzle-v2",mode,source,skill,phase,piece_focus,
                                    str(difficulty),str(mate_in),str(challenge_score),
                                    str(challenge_score if challenge_mode else request.get("token",0)),
                                    ",".join(source_fens),
                                    ",".join(map(str,recent_themes))))
            rng=random.Random(hashlib.sha256(seed_material.encode("utf-8")).hexdigest())
            candidates=[];seen=set()
            for item in source_positions if source=="games" else []:
                if isinstance(item,dict):fen=item.get("fen");source_label=item.get("source","a saved game")
                else:fen=item;source_label="a saved game"
                try:board=chess.Board(fen)
                except (ValueError,TypeError):continue
                key=position_key(board)
                if key not in seen and board.fen() not in recent_fens and not board.is_game_over() and len(board.piece_map())>=6:
                    seen.add(key);candidates.append((board,source_label,item if isinstance(item,dict) else {}))
            random_count=((24+difficulty*3) if challenge_mode else (12+difficulty*2)) if source=="generated" else 0
            if mode=="mate" and source=="generated":random_count=max(random_count,12+mate_in*5)
            for _ in range(random_count):
                board=chess.Board();target_plies=rng.randint(8,52+difficulty*2)
                for _ply in range(target_plies):
                    if board.is_game_over():break
                    legal=list(board.legal_moves)
                    if not legal:break
                    board.push(rng.choice(legal))
                key=position_key(board)
                if key not in seen and board.fen() not in recent_fens and not board.is_game_over() and len(board.piece_map())>=8:
                    seen.add(key);candidates.append((board,"a generated legal game position",{}))
            rng.shuffle(candidates)
            attempts=min(len(candidates),(24+difficulty*3) if challenge_mode else (18+difficulty*2))
            if challenge_mode:
                if source=="generated":candidates=[item for item in candidates if item[1]=="a generated legal game position"]
                attempts=min(len(candidates),min(30,18+difficulty))
            if mode=="random" and source=="generated":
                candidates=[item for item in candidates if item[1]=="a generated legal game position"]
                attempts=min(len(candidates),20+difficulty*2)
            elif mode=="personal" and source=="games":
                candidates=[item for item in candidates if item[1]=="a downloaded Chess.com game"]
                candidates.sort(key=lambda item:(not item[2].get("mistake",False),item[0].fen()))
                attempts=min(len(candidates),max(12,30+difficulty*3))
            elif mode=="mate":
                # The caller chooses saved PGNs or scratch positions for every mode.
                attempts=min(len(candidates),12+mate_in*5)
            full_strength={}
            if "UCI_LimitStrength" in self.option_names:full_strength["UCI_LimitStrength"]=False
            if "Skill Level" in self.option_names:full_strength["Skill Level"]=20
            restore={}
            if "Threads" in self.option_names:
                full_strength["Threads"]=1
                restore["Threads"]=max(1,int(self.engine.options["Threads"].default or 1))
            if "UCI_LimitStrength" in self.option_names:restore["UCI_LimitStrength"]=True
            if "UCI_Elo" in self.option_names:restore["UCI_Elo"]=max(self._uci_elo_min,min(self._uci_elo_max,self._elo))
            if "UCI_Elo" in self.option_names:
                option=self.engine.options["UCI_Elo"]
                full_strength["UCI_Elo"]=int(option.max or self._uci_elo_max)
            if "Skill Level" in self.option_names:
                skill_level=0 if self._elo<MIN_ELO else round((self._elo-MIN_ELO)/(MAX_ELO-MIN_ELO)*20)
                option=self.engine.options["Skill Level"]
                restore["Skill Level"]=max(int(option.min or 0),min(int(option.max or 20),skill_level))
            if full_strength:self.engine.configure(full_strength)
            puzzle=None;best_match=None;challenge_pool=[]
            target_gap=max(5,130-11*(difficulty-1)-3*max(0,challenge_score-9))
            budget=(max(.45,min(1.1,.35+.15*mate_in)) if mode=="mate" else
                    min(.9,.16+.04*difficulty+.01*max(0,challenge_score-9)) if challenge_mode else .16+.04*difficulty)
            try:
                for index,(board,source_label,metadata) in enumerate(candidates[:attempts],1):
                    if self.stop_event.is_set():break
                    multipv=min(3,self._multipv_max)
                    limit=chess.engine.Limit(time=budget)
                    info=self.engine.analyse(board,limit,multipv=multipv) if "MultiPV" in self.option_names else self.engine.analyse(board,limit)
                    lines=info if isinstance(info,list) else [info]
                    lines=[line for line in lines if line.get("pv") and line["pv"][0] in board.legal_moves and line.get("score")]
                    self.puzzle_progress.emit(token,index,attempts)
                    if not lines:continue
                    best_line=lines[0];best_move=best_line["pv"][0];themes=tactical_themes(board,best_move)
                    if board.is_castling(best_move):themes.add("Castling")
                    mate_score=best_line["score"].pov(board.turn).mate()
                    if mate_score is not None:themes.add("Mate")
                    if mode=="mate" and (mate_score is None or mate_score<=0 or mate_score>mate_in):continue
                    if mode=="focus" and skill!="Any tactic" and skill not in themes:continue
                    if mode=="focus" and skill=="Any tactic" and not themes:continue
                    if mode=="focus" and phase:
                        fullmove=metadata.get("move_number",board.fullmove_number)
                        candidate_phase=metadata.get("phase",game_phase(board))
                        if phase=="Opening" and not (candidate_phase=="Opening" and 3<=fullmove<=6):continue
                        if phase!="Opening" and candidate_phase!=phase:continue
                    if mode=="focus" and piece_focus:
                        moving_piece=board.piece_at(best_move.from_square)
                        if not moving_piece or moving_piece.symbol().lower()!=piece_focus:continue
                    personal_skill_match=(skill=="Any tactic" or skill in themes)
                    candidate_phase=metadata.get("phase",game_phase(board))
                    fullmove=metadata.get("move_number",board.fullmove_number)
                    personal_phase_match=(not phase or
                                          (candidate_phase=="Opening" and 3<=fullmove<=6 if phase=="Opening"
                                           else candidate_phase==phase))
                    if challenge_mode and not themes:continue
                    best_score=best_line["score"].pov(board.turn).score(mate_score=10000)
                    if best_score is None:continue
                    second_score=lines[1]["score"].pov(board.turn).score(mate_score=10000) if len(lines)>1 else best_score-100
                    if second_score is None:second_score=best_score-100
                    gap=best_score-second_score
                    if best_score is not None and best_score<-350 and "Mate" not in themes:continue
                    pv=best_line["pv"][:9];pv_board=board.copy();pv_sans=[]
                    for pv_move in pv:
                        if pv_move not in pv_board.legal_moves:break
                        pv_sans.append(pv_board.san(pv_move));pv_board.push(pv_move)
                    theme_order=("Mate","Castling","Fork","Pin","Skewer","Discovered attack","Promotion","Check","Winning material")
                    available_themes=[name for name in theme_order if name in themes]
                    fresh_themes=[name for name in available_themes if name not in recent_themes]
                    theme=(skill if skill!="Any tactic" and (mode!="personal" or personal_skill_match)
                           else next(iter(fresh_themes or available_themes),"Tactic"))
                    candidate_puzzle={"fen":board.fen(),"move":best_move.uci(),"san":board.san(best_move),"theme":theme,
                                      "themes":sorted(themes),"pv":pv_sans,"difficulty":difficulty,"gap":gap,"source":source_label,
                                      "difficulty_match":abs(gap-target_gap)<=40,
                                      "mate_in":mate_score if mate_score and mate_score>0 else None}
                    candidate_puzzle["difficulty_distance"]=abs(gap-target_gap)
                    if mode=="mate":
                        candidate_puzzle["difficulty_distance"]=(mate_in-mate_score)*10000+abs(gap-target_gap)
                    if mode=="personal":
                        candidate_puzzle["difficulty_distance"]+=((0 if personal_skill_match else 5000)
                                                                    +(0 if personal_phase_match else 2500))
                    if best_match is None or candidate_puzzle["difficulty_distance"]<best_match["difficulty_distance"]:best_match=candidate_puzzle
                    if mode=="mate" and mate_score==mate_in:
                        puzzle=candidate_puzzle
                        break
                    if candidate_puzzle["difficulty_match"]:
                        if challenge_mode:
                            challenge_pool.append(candidate_puzzle)
                            if candidate_puzzle["theme"] not in recent_themes:break
                        elif mode=="mate":
                            if puzzle is None or candidate_puzzle.get("mate_in",0)>puzzle.get("mate_in",0):puzzle=candidate_puzzle
                        elif mode=="personal":
                            if personal_skill_match and personal_phase_match:
                                puzzle=candidate_puzzle
                                break
                        else:puzzle=candidate_puzzle;break
            finally:
                if restore:self.engine.configure(restore)
            if challenge_pool:
                varied=[item for item in challenge_pool if item["theme"] not in recent_themes]
                pool=varied or challenge_pool
                best_distance=min(item["difficulty_distance"] for item in pool)
                closest=[item for item in pool if item["difficulty_distance"]<=best_distance+15]
                puzzle=rng.choice(closest)
            if puzzle is None:puzzle=best_match
            if mode=="mate":error="No forced mate was found at that distance. Try a shorter mate target or generate another position."
            elif mode=="personal":error="No suitable move was found in the downloaded games. Review more games or generate another puzzle."
            elif mode=="focus":error="No position matched that focus this time. Try a different theme, phase, or piece."
            else:error="No suitable random puzzle was found this time. Generate another puzzle."
            self.puzzle_done.emit(token,puzzle,"" if puzzle else error)
        except Exception as exc:
            self.puzzle_done.emit(token,None,f"Puzzle generation failed: {exc}")

    @pyqtSlot()
    def shutdown(self):
        if self.engine:
            try: self.engine.quit()
            except Exception:
                try: self.engine.close()
                except Exception: pass
            self.engine = None
        QThread.currentThread().quit()

__all__ = ["EngineWorker"]
