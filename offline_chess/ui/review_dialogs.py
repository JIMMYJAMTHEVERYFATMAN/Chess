"""Move practice, puzzle, review progress, and summary dialogs."""
from __future__ import annotations

import json
import os
from datetime import datetime

import chess
import chess.pgn
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, pyqtSlot, QObject, QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QAction, QPolygonF, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QSlider, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QProgressBar,
    QCheckBox, QSpinBox, QComboBox, QAbstractItemView, QScrollArea, QSizePolicy,
    QStackedWidget, QToolTip, QMenu, QTabWidget, QLineEdit,
)

from ..review_logic import *
from .board import ChessBoard
from .common import fit_dialog_to_screen
from .theme import *

class MistakePracticeDialog(QDialog):
    """Replay review errors and missed chances as retryable positions before revealing the line."""
    analysis_requested=pyqtSignal(object)
    def __init__(self,start_board,moves,history,parent=None,start_ply=None,include_all=False,
                 player_color=chess.WHITE):
        super().__init__(parent);self.setWindowTitle("Practice key positions");fit_dialog_to_screen(self,parent,1200,1050)
        self.start_board=start_board.copy(stack=False);self.history=list(history)
        self.player_color=player_color
        self.board_flipped=player_color==chess.BLACK
        self.items=list(moves) if include_all else [item for item in moves if item.verdict in ("Miss","Inaccuracy","Mistake","Blunder")]
        self.index=next((i for i,item in enumerate(self.items) if item.ply==start_ply),0);self.position=None;self.selected=None;self.revealed=False;self.line_board=None;self.line_moves=[];self.feedback_token=0;self.feedback_pending=False;self.last_attempt=None;self.hint_stage=0
        layout=QVBoxLayout(self);self.heading=QLabel();self.heading.setObjectName("Brand");layout.addWidget(self.heading)
        self.prompt=QLabel("Find a stronger move. Try before revealing the engine suggestion.");self.prompt.setWordWrap(True);self.prompt.setObjectName("ReviewSummary");layout.addWidget(self.prompt)
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();board_side=max(400,min(780,int(screen.availableGeometry().height()*.52))) if screen else 600
        self.board=ChessBoard();self.board.flipped=self.board_flipped;self.board.setMinimumSize(board_side,board_side);self.board.square_clicked.connect(self.on_square);layout.addWidget(self.board,1)
        status_row=QHBoxLayout();self.status=QLabel();self.status.setObjectName("Muted");self.status.setWordWrap(True);status_row.addWidget(self.status,1)
        self.flip_btn=QPushButton("Flip board");self.flip_btn.clicked.connect(self.flip_board)
        self.flip_btn.setToolTip(f"{'Black' if self.board_flipped else 'White'} is at the bottom. Click to flip.")
        status_row.addWidget(self.flip_btn);layout.addLayout(status_row)
        row=QHBoxLayout();self.hint_btn=QPushButton("Hint");self.hint_btn.clicked.connect(self.show_hint);row.addWidget(self.hint_btn)
        self.reveal_btn=QPushButton("Show best move");self.reveal_btn.clicked.connect(self.reveal);row.addWidget(self.reveal_btn)
        self.line_btn=QPushButton("Play best line");self.line_btn.clicked.connect(self.play_line_step);self.line_btn.setEnabled(False);row.addWidget(self.line_btn)
        row.addStretch();self.next_btn=QPushButton("Next position");self.next_btn.setObjectName("PrimaryButton");self.next_btn.clicked.connect(self.next_position);row.addWidget(self.next_btn);done=QPushButton("Close");done.clicked.connect(self.accept);row.addWidget(done);layout.addLayout(row)
        self._load_position()

    def flip_board(self):
        self.board_flipped=not self.board_flipped
        self.board.flipped=self.board_flipped;self.board.update()
        self.flip_btn.setToolTip(f"{'Black' if self.board_flipped else 'White'} is at the bottom. Click to flip.")

    def _load_position(self):
        self.feedback_token+=1;self.feedback_pending=False
        if self.index>=len(self.items):
            self.heading.setText("Practice complete");self.prompt.setText("You’ve revisited every reviewed mistake. You can close this session now.");self.status.setText(f"Completed {count_label(len(self.items),'position')}.");self.board.set_position(self.start_board);self.next_btn.setEnabled(False);self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False);self.line_btn.setEnabled(False);return
        item=self.items[self.index];self.position=self.start_board.copy(stack=False)
        try:
            for move in self.history[:item.ply-1]:self.position.push(move)
        except (ValueError,IndexError):self.position=self.start_board.copy(stack=False)
        self.selected=None;self.revealed=False;self.line_board=None;self.line_moves=[];self.last_attempt=None;self.hint_stage=0
        self.heading.setText(f"Position {self.index+1} of {len(self.items)} · {review_move_notation(item)} · {item.verdict}")
        self.prompt.setText(f"{item.side} to move. The game move was {item.san}. Find the engine’s top move or an equally strong choice before revealing it.")
        self.status.setText("Select one of your pieces, then its destination. Try freely; your move stays local.")
        self.board.set_position(self.position,last_move=None,selected=None,best_move=None,review_sticker=None)
        self.next_btn.setText("Next position" if self.index+1<len(self.items) else "Finish")
        self.line_btn.setEnabled(False);self.reveal_btn.setEnabled(True);self.reveal_btn.setText("Show best move");self.hint_btn.setEnabled(True);self.hint_btn.setText("Hint")

    def on_square(self,square):
        if self.position is None or self.revealed:return
        piece=self.position.piece_at(square)
        if self.selected is None:
            if piece and piece.color==self.position.turn:self.selected=square
        else:
            candidate=chess.Move(self.selected,square)
            item=self.items[self.index]
            if self.position.piece_type_at(self.selected)==chess.PAWN and chess.square_rank(square) in (0,7):
                candidate.promotion=item.best_move.promotion if item.best_move.from_square==self.selected and item.best_move.to_square==square else chess.QUEEN
            if candidate in self.position.legal_moves:
                if candidate in (item.acceptable_moves or (item.best_move,)):
                    before=self.position.copy(stack=False)
                    after=before.copy(stack=False);after.push(candidate)
                    if candidate==item.best_move:
                        response_pv=tuple(getattr(item,"best_pv_moves",())[1:10])
                        response=response_pv[0] if response_pv and response_pv[0] in after.legal_moves else None
                        events=analyze_review_events(before,candidate,after,candidate,response,
                                                     tuple(getattr(item,"best_pv_moves",())[:10]),
                                                     response_pv=response_pv[1:] if response else ())
                        stored=[event for event in getattr(item,"events",())
                                if event.severity=="positive" and event.kind=="sound_sacrifice"]
                        ideas=stored or [event for event in events if event.severity=="positive"]
                    else:ideas=[]
                    self.position.push(candidate);self.revealed=True
                    if candidate==item.best_move:
                        detail=f" {review_text_for_viewer(ideas[0].text,item,self.player_color)}" if ideas else ""
                        self.status.setText(f"That’s the engine’s top move.{detail} Now compare the continuation or move on.");sticker="Top move"
                    else:
                        self.status.setText("Strong choice. The engine rates this move effectively equal to its top line.");sticker="Strong choice"
                    self.board.set_position(self.position,last_move=candidate,selected=None,best_move=None,review_sticker=sticker,sticker_color="#39a982");self.selected=None
                    self.hint_btn.setEnabled(False);self.reveal_btn.setText("Show best line");return
                self.feedback_token+=1;self.feedback_pending=True;self.selected=None
                self.last_attempt=candidate
                self.status.setText("Checking that move against Stockfish…")
                self.board.set_position(self.position,last_move=candidate,selected=None,best_move=None,review_sticker="Analysing",sticker_color="#e0b746")
                self.board.setEnabled(False);self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False);self.next_btn.setEnabled(False)
                budget=next((seconds for label,seconds in (("Quick estimate",.12),
                              ("Standard analysis",.4),("Thorough analysis",.8))
                             if item.confidence.startswith(label)),.24)
                self.analysis_requested.emit((self.feedback_token,self.position.copy(stack=False),candidate,item.best_move,budget))
                return
            self.selected=square if piece and piece.color==self.position.turn else None
        self.board.set_position(self.position,last_move=None,selected=self.selected,best_move=None)

    def apply_engine_feedback(self,result):
        if result.get("token")!=self.feedback_token:return
        self.feedback_pending=False;self.board.setEnabled(True);self.hint_btn.setEnabled(self.hint_stage<2);self.reveal_btn.setEnabled(True);self.next_btn.setEnabled(True)
        item=self.items[self.index]
        self.status.setText(review_text_for_viewer(result.get("feedback","Stockfish could not analyze that attempt."),item,self.player_color))
        if result.get("acceptable") and self.last_attempt in self.position.legal_moves:
            self.position.push(self.last_attempt);self.revealed=True
            self.hint_btn.setEnabled(False);self.reveal_btn.setText("Show best line")
            self.board.set_position(self.position,last_move=self.last_attempt,selected=None,best_move=None,
                                    review_sticker="Strong choice",sticker_color="#39a982")
            return
        self.board.set_position(self.position,last_move=self.last_attempt,selected=None,best_move=None,review_sticker="Try again",sticker_color="#e0b746")

    def show_hint(self):
        item=self.items[self.index]
        if self.hint_stage>=2:return
        self.hint_stage+=1
        clue=review_practice_hint(self.position,item.best_move,item.best_pv_moves,self.hint_stage)
        self.status.setText(f"Hint {self.hint_stage}: {clue} Try to improve on the move that was played.")
        if self.hint_stage==1:self.hint_btn.setText("Next hint")
        else:self.hint_btn.setText("Hints used");self.hint_btn.setEnabled(False)

    def reveal(self):
        item=self.items[self.index];self.revealed=True;self.selected=None
        self.hint_btn.setEnabled(False);self.reveal_btn.setEnabled(False)
        explanation=review_text_for_viewer(item.explanation,item,self.player_color)
        self.status.setText(f"Best move: {item.best_san}. {explanation} Best line: {item.pv or item.best_san}")
        self.board.set_position(self.position,last_move=None,selected=None,best_move=item.best_move,review_sticker="Best move",sticker_color="#39a982")
        line_board=self.position.copy();self.line_moves=[]
        for san in item.pv.split():
            try:move=line_board.parse_san(san)
            except ValueError:break
            self.line_moves.append(move);line_board.push(move)
        self.line_board=self.position.copy();self.line_btn.setEnabled(bool(self.line_moves));self.line_btn.setText("Play best line" if self.line_moves else "No line available")

    def play_line_step(self):
        if not self.line_moves or self.line_board is None:return
        move=self.line_moves.pop(0);self.line_board.push(move)
        self.board.set_position(self.line_board,last_move=move,selected=None,best_move=None,review_sticker="Best line",sticker_color="#39a982")
        self.status.setText("Playing the engine’s suggested continuation, one move at a time.")
        if not self.line_moves:self.line_btn.setText("Line complete");self.line_btn.setEnabled(False)

    def next_position(self):
        self.index+=1;self._load_position()


class PuzzleDialog(QDialog):
    puzzle_requested=pyqtSignal(object)
    def __init__(self,theme_key,parent=None):
        super().__init__(parent);self.setWindowTitle("Offline Puzzles · Skill Practice");fit_dialog_to_screen(self,parent,1800,1250)
        self.token=0;self.puzzle=None;self.base_board=None;self.solution=None;self.selected=None;self.solved=False;self.revealed=False
        self.challenge_active=False;self.challenge_score=0;self.challenge_level=1;self.lives=3;self.recent_challenge_themes=[]
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();board_side=max(480,min(920,int(screen.availableGeometry().height()*.65))) if screen else 700
        root=QHBoxLayout(self);root.setContentsMargins(26,24,26,24);root.setSpacing(26)
        self.board=ChessBoard();self.board.setMinimumSize(board_side,board_side);self.board.set_theme(theme_key);self.board.square_clicked.connect(self.on_square);root.addWidget(self.board,1)
        panel=QFrame();panel.setObjectName("SidePanel");panel.setMinimumWidth(420);panel.setMaximumWidth(620);col=QVBoxLayout(panel);col.setContentsMargins(24,24,24,24);col.setSpacing(16)
        title=QLabel("PUZZLE STUDIO");title.setObjectName("Brand");col.addWidget(title)
        intro=QLabel("Choose a chess skill and difficulty. Puzzles mix positions from your games with new legal positions generated locally by Stockfish.");intro.setObjectName("Muted");intro.setWordWrap(True);col.addWidget(intro)
        skill_label=QLabel("Practice skill");skill_label.setObjectName("PlayerName");col.addWidget(skill_label)
        self.skill_choice=QComboBox()
        for label,value in (("Any tactic","Any tactic"),("Checkmate","Mate"),("Fork","Fork"),("Pin","Pin"),("Discovered attack","Discovered attack"),("Win material","Winning material"),("Promotion","Promotion")):
            self.skill_choice.addItem(label,value)
        col.addWidget(self.skill_choice)
        difficulty_row=QHBoxLayout();difficulty_title=QLabel("Difficulty");difficulty_title.setObjectName("PlayerName");difficulty_row.addWidget(difficulty_title);difficulty_row.addStretch();self.difficulty_label=QLabel("5 · Club");self.difficulty_label.setObjectName("AccentText");difficulty_row.addWidget(self.difficulty_label);col.addLayout(difficulty_row)
        self.difficulty=QSlider(Qt.Orientation.Horizontal);self.difficulty.setRange(1,10);self.difficulty.setValue(5);self.difficulty.valueChanged.connect(self._difficulty_changed);col.addWidget(self.difficulty)
        marks=QHBoxLayout();marks.addWidget(QLabel("Easy"));marks.addStretch();marks.addWidget(QLabel("Hard"));col.addLayout(marks)
        self.generate_btn=QPushButton("Generate puzzle");self.generate_btn.setObjectName("PrimaryButton");self.generate_btn.clicked.connect(self.generate);col.addWidget(self.generate_btn)
        self.challenge_btn=QPushButton("Start Challenge");self.challenge_btn.setObjectName("PrimaryButton");self.challenge_btn.clicked.connect(self.start_challenge);col.addWidget(self.challenge_btn)
        self.hint_btn=QPushButton("Hint");self.hint_btn.clicked.connect(self.show_hint);self.hint_btn.setEnabled(False);col.addWidget(self.hint_btn)
        self.solution_btn=QPushButton("Show solution");self.solution_btn.clicked.connect(self.show_solution);self.solution_btn.setEnabled(False);col.addWidget(self.solution_btn)
        self.progress=QProgressBar();self.progress.setTextVisible(True);self.progress.setFormat("Ready");self.progress.setValue(0);col.addWidget(self.progress)
        self.challenge_score_label=QLabel("Challenge score: 0");self.challenge_score_label.setObjectName("AccentText");col.addWidget(self.challenge_score_label)
        self.lives_label=QLabel("Lives: 3 / 3");self.lives_label.setObjectName("AccentText");col.addWidget(self.lives_label)
        self.status=QLabel("Generate a puzzle to start.");self.status.setObjectName("ReviewSummary");self.status.setWordWrap(True);self.status.setMinimumHeight(90);col.addWidget(self.status)
        self.line=QLabel("");self.line.setObjectName("Muted");self.line.setWordWrap(True);col.addWidget(self.line)
        col.addStretch();close=QPushButton("Close");close.clicked.connect(self.accept);col.addWidget(close);root.addWidget(panel)

    def _difficulty_changed(self,value):
        band="Beginner" if value<=2 else "Easy" if value<=4 else "Club" if value<=6 else "Challenging" if value<=8 else "Advanced"
        self.difficulty_label.setText(f"{value} · {band}")

    def generate(self):
        if self.generate_btn.isEnabled() is False:return
        self.token+=1;self.puzzle=None;self.base_board=None;self.solution=None;self.selected=None;self.solved=False;self.revealed=False
        self.board.setEnabled(False);self.board.set_position(chess.Board(),review_sticker=None);self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(False);self.line.setText("")
        if self.challenge_active:
            self.status.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10. Find the best move.")
        else:self.status.setText("Searching your local games and generating legal positions…")
        self.progress.setRange(0,0);self.progress.setFormat("Generating puzzle…");self.generate_btn.setEnabled(False)
        skill="Any tactic" if self.challenge_active else self.skill_choice.currentData()
        difficulty=self.challenge_level if self.challenge_active else self.difficulty.value()
        self.puzzle_requested.emit((self.token,skill,difficulty,self.challenge_active,list(self.recent_challenge_themes)))

    def start_challenge(self):
        if self.challenge_active:return
        self.challenge_active=True;self.challenge_score=0;self.challenge_level=1
        self.recent_challenge_themes=[]
        self.challenge_score_label.setText("Challenge score: 0 · Level 1/10")
        self.skill_choice.setEnabled(False);self.difficulty.setEnabled(False);self.challenge_btn.setEnabled(False)
        self.generate_btn.setText("Generating challenge…");self.generate()

    def _next_challenge_puzzle(self):
        if not self.challenge_active:return
        self.challenge_level=min(10,1+self.challenge_score)
        self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10")
        self.generate_btn.setText("Generating challenge…");self.generate_btn.setEnabled(True);self.generate()

    def _challenge_failed(self):
        self.challenge_active=False;self.board.setEnabled(False)
        self.skill_choice.setEnabled(True);self.difficulty.setEnabled(True)
        self.challenge_btn.setEnabled(True);self.challenge_btn.setText("Play Again")
        self.generate_btn.setText("Generate puzzle");self.generate_btn.setEnabled(True)
        self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(self.puzzle is not None)
        self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · final")
        self.status.setText(f"Challenge over · You solved {self.challenge_score} puzzle(s). You used all 3 lives on this puzzle.")

    def update_progress(self,token,done,total):
        if token!=self.token:return
        self.progress.setRange(0,max(1,total));self.progress.setValue(done);self.progress.setFormat(f"Checking positions · {done}/{total}")

    def show_puzzle(self,token,puzzle,error):
        if token!=self.token:return
        self.generate_btn.setEnabled(True);self.board.setEnabled(True);self.progress.setRange(0,1);self.progress.setValue(1)
        if not puzzle:
            self.puzzle=None;self.status.setText(error or "No matching puzzle was found.");self.progress.setFormat("No puzzle found")
            if self.challenge_active:
                self.status.setText(f"Could not generate a level {self.challenge_level} puzzle. Your score remains {self.challenge_score}; retrying the same level is safe.")
                self.generate_btn.setText("Retry challenge puzzle");self.generate_btn.setEnabled(True)
            return
        self.puzzle=puzzle;self.base_board=chess.Board(puzzle["fen"]);self.solution=chess.Move.from_uci(puzzle["move"]);self.selected=None;self.solved=False;self.revealed=False
        self.lives=3;self.lives_label.setText("Lives: 3 / 3")
        self.board.flipped=self.base_board.turn==chess.BLACK
        self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=None)
        level_note="Matches the selected difficulty." if puzzle.get("difficulty_match") else "Closest match found; difficulty is approximate."
        self.status.setText(f"{puzzle['theme']} · {puzzle['difficulty']}/10 · {level_note} Find the best move for the side to play.")
        self.progress.setFormat("Puzzle ready");self.hint_btn.setEnabled(not self.challenge_active);self.solution_btn.setEnabled(not self.challenge_active)
        if self.challenge_active:
            self.challenge_btn.setText(f"Challenge · {self.challenge_score} solved");self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · Level {self.challenge_level}/10")
            self.recent_challenge_themes.append(puzzle["theme"]);self.recent_challenge_themes=self.recent_challenge_themes[-2:]
            level_note="" if puzzle.get("difficulty_match") else " · approximate level"
            self.status.setText(f"{puzzle['theme']} puzzle · score {self.challenge_score} · level {self.challenge_level}/10{level_note}. Find the best move.")
            self.generate_btn.setText("Challenge in progress");self.generate_btn.setEnabled(False)
        self.line.setText(f"The position came from {puzzle.get('source','a generated legal game position')}.")

    def on_square(self,square):
        if self.base_board is None or self.solved or self.revealed:return
        piece=self.base_board.piece_at(square)
        if self.selected is None:
            if piece and piece.color==self.base_board.turn:self.selected=square
        else:
            candidate=chess.Move(self.selected,square)
            if self.base_board.piece_type_at(self.selected)==chess.PAWN and chess.square_rank(square) in (0,7):
                candidate.promotion=self.solution.promotion if self.solution.from_square==self.selected and self.solution.to_square==square else chess.QUEEN
            if candidate in self.base_board.legal_moves:
                if candidate==self.solution:
                    self.solved=True;solved_board=self.base_board.copy();solved_board.push(candidate)
                    self.board.set_position(solved_board,last_move=candidate,selected=None,best_move=None,review_sticker="Correct",sticker_color="#39a982")
                    self.status.setText("Correct. You found the engine’s top move.");self.line.setText("Best line: "+" ".join(self.puzzle.get("pv",[])));self.selected=None
                    if self.challenge_active:
                        self.challenge_score+=1;self.challenge_score_label.setText(f"Challenge score: {self.challenge_score} · next level {min(10,1+self.challenge_score)}/10")
                        self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(False);self.board.setEnabled(False)
                        self.status.setText(f"Correct · score {self.challenge_score}. Next puzzle is harder…")
                        QTimer.singleShot(900,self._next_challenge_puzzle)
                    return
                if self.challenge_active:
                    self.lives-=1;self.lives_label.setText(f"Lives: {self.lives} / 3")
                    if self.lives<=0:
                        self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker="Out of lives",sticker_color="#de6268")
                        self._challenge_failed();return
                    self.status.setText(f"Not the best move · {self.lives} lives left. Keep looking.")
                    self.selected=None;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=f"{self.lives} lives left",sticker_color="#e0b746");return
                self.lives-=1;self.lives_label.setText(f"Lives: {self.lives} / 3")
                if self.lives<=0:
                    self.solved=True;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker="Out of lives",sticker_color="#de6268");self.board.setEnabled(False)
                    self.status.setText("Puzzle over · you used all 3 lives. Reveal the solution or generate another puzzle.")
                    self.hint_btn.setEnabled(False);self.solution_btn.setEnabled(True);self.generate_btn.setEnabled(True);return
                self.status.setText(f"That move misses the strongest continuation · {self.lives} lives left. Try again or ask for a hint.")
                self.selected=None;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=None,review_sticker=f"{self.lives} lives left",sticker_color="#e0b746");return
            self.selected=square if piece and piece.color==self.base_board.turn else None
        self.board.set_position(self.base_board,last_move=None,selected=self.selected,best_move=None)

    def show_hint(self):
        if not self.puzzle:return
        hints={"Mate":"Look for forcing checks. Count the king’s escape squares after each check.",
               "Check":"Look for a forcing check that also creates a follow-up threat or wins time.",
               "Fork":"Look for a move that attacks two valuable targets at once, especially with check.",
               "Pin":"Can you line up a rook, bishop, or queen with a valuable piece behind the target?",
               "Discovered attack":"Move a blocker to uncover an attack from a rook, bishop, or queen.",
               "Winning material":"Look for a forcing capture or a threat that wins a valuable piece.",
               "Promotion":"Check whether a pawn can promote safely or create a promotion threat."}
        self.status.setText(hints.get(self.puzzle["theme"],"Look for checks, captures, and threats before choosing a quiet move."))

    def show_solution(self):
        if not self.puzzle:return
        self.revealed=True;self.board.set_position(self.base_board,last_move=None,selected=None,best_move=self.solution,review_sticker="Best move",sticker_color="#39a982")
        self.status.setText(f"Best move: {self.puzzle['san']} · {', '.join(self.puzzle.get('themes',[]))}")
        self.line.setText("Best line: "+" ".join(self.puzzle.get("pv",[])))


class ReviewProgressDialog(QDialog):
    cancel_requested=pyqtSignal()
    def __init__(self,parent=None,batch_total=0,batch_started_at=None):
        super().__init__(parent);self.finished=False;self.batch_total=max(0,int(batch_total));self.batch_started_at=batch_started_at or time.monotonic();self.setWindowTitle("Reviewing downloaded games" if self.batch_total else "Reviewing game");self.setModal(True);self.setWindowModality(Qt.WindowModality.ApplicationModal);self.setFixedWidth(380)
        self.started_at=time.monotonic()
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint,False)
        layout=QVBoxLayout(self);title=QLabel("Analysing both sides, one move at a time…");title.setObjectName("PlayerName");layout.addWidget(title)
        self.status=QLabel("Preparing Stockfish review");self.status.setObjectName("Muted");layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);layout.addWidget(self.progress)
        self.batch_status=None;self.batch_progress=None
        if self.batch_total:
            self.batch_status=QLabel("Preparing batch estimate…");self.batch_status.setObjectName("Muted");self.batch_status.setWordWrap(True);layout.addWidget(self.batch_status)
            self.batch_progress=QProgressBar();self.batch_progress.setRange(0,self.batch_total*100);self.batch_progress.setValue(0);layout.addWidget(self.batch_progress)
        note=QLabel("Analysis runs locally. You can stop early and keep the moves already reviewed.");note.setObjectName("Muted");note.setWordWrap(True);layout.addWidget(note)
        self.cancel_button=QPushButton("Cancel review");self.cancel_button.clicked.connect(self.request_cancel);layout.addWidget(self.cancel_button)
    def begin_game(self):
        self.started_at=time.monotonic();self.status.setText("Preparing Stockfish review");self.progress.setRange(0,100);self.progress.setValue(0)
    def update_progress(self,done,total):
        elapsed=max(.01,time.monotonic()-self.started_at)
        remaining=max(0,int((elapsed/max(1,done))*(total-done)))
        estimate=f" · about {remaining}s left" if done else ""
        self.status.setText(f"Analysing move {done} of {total}{estimate}…");self.progress.setRange(0,max(1,total));self.progress.setValue(done)
    def update_batch_progress(self,completed_games,move_done=0,move_total=0):
        if not self.batch_total or not self.batch_progress or not self.batch_status:return
        fraction=min(float(self.batch_total),max(0.0,float(completed_games)))
        if move_total:fraction=min(float(self.batch_total),fraction+max(0,min(1,move_done/max(1,move_total))))
        self.batch_progress.setValue(round(fraction*100))
        elapsed=max(.01,time.monotonic()-self.batch_started_at)
        remaining=max(0,round((elapsed/max(fraction,.01))*(self.batch_total-fraction)))
        eta=(f"about {remaining//60}m {remaining%60}s remaining" if remaining>=60
             else f"about {remaining}s remaining")
        finished_games=int(fraction)
        if fraction>=self.batch_total:
            progress_text=f"{self.batch_total} of {self.batch_total} games complete"
        elif fraction>finished_games:
            current_percent=round((fraction-finished_games)*100)
            progress_text=f"Game {finished_games+1} of {self.batch_total} · {current_percent}% complete"
        else:
            progress_text=f"{finished_games} of {self.batch_total} games complete"
        self.batch_status.setText(f"{progress_text} · {eta}")
    def closeEvent(self,event):
        if not self.finished:event.ignore()
        else:event.accept()
    def request_cancel(self):
        if not self.finished and self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False);self.cancel_button.setText("Stopping…")
            self.status.setText("Stopping after the current position…")
            self.cancel_requested.emit()
    def finish(self):
        self.finished=True;self.accept()


class ReviewSummaryDialog(QDialog):
    def __init__(self,items,opening,result,user_color=chess.WHITE,parent=None,opening_ply=0,
                 analysis_level="Standard",syzygy_enabled=False):
        super().__init__(parent);self.setWindowTitle("Game Review · Summary");self.setModal(True);self.selected_ply=None;fit_dialog_to_screen(self,parent,1500,720)
        root=QVBoxLayout(self)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(0,0,0,0)
        scroll.setWidget(body);root.addWidget(scroll,1)
        title=QLabel("Game review complete");title.setObjectName("Brand");layout.addWidget(title)
        user_side="White" if user_color==chess.WHITE else "Black"
        if result=="1-0":outcome="You won as White" if user_color==chess.WHITE else "You lost as Black"
        elif result=="0-1":outcome="You won as Black" if user_color==chess.BLACK else "You lost as White"
        elif result=="1/2-1/2":outcome=f"Draw · you played as {user_side}"
        else:outcome=f"Game in progress · you played as {user_side}"
        tablebase_note=" · Syzygy available" if syzygy_enabled else ""
        subtitle=QLabel(f"{opening}  ·  {outcome}  ·  {analysis_level} analysis{tablebase_note}");subtitle.setObjectName("Muted");layout.addWidget(subtitle)
        cards=QHBoxLayout()
        def side_card(side):
            moves=[item for item in items if item.side==side]
            accuracy=review_accuracy(moves)
            errors=sum(item.verdict in ("Inaccuracy","Mistake","Blunder") for item in moves)
            missed=sum(item.verdict=="Miss" for item in moves)
            frame=QFrame();frame.setObjectName("MetricCard");column=QVBoxLayout(frame);column.setContentsMargins(16,12,16,12)
            perspective="YOU" if side==user_side else "OPPONENT"
            label=QLabel(f"{perspective} · {side.upper()} · MOVE QUALITY");label.setObjectName("MetricCaption");column.addWidget(label)
            score=QLabel(f"{accuracy:.1f}%" if accuracy is not None else "—");score.setObjectName("MetricValue");column.addWidget(score)
            note=QLabel(f"{errors} error{'s' if errors!=1 else ''} · {missed} missed chance{'s' if missed!=1 else ''} · {len(moves)} moves");note.setObjectName("Muted");column.addWidget(note)
            frame.setToolTip(MOVE_QUALITY_TOOLTIP)
            return frame
        opponent_side="Black" if user_side=="White" else "White"
        cards.addWidget(side_card(user_side));cards.addWidget(side_card(opponent_side));layout.addLayout(cards)
        quality_note=QLabel("Local move quality is a CAPS2-inspired grade from Stockfish evaluation changes, not a literal expected-score percentage or player rating.")
        quality_note.setObjectName("Muted");quality_note.setWordWrap(True);layout.addWidget(quality_note)
        table=QTableWidget(2,13);table.setHorizontalHeaderLabels(["Side","Move quality","Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracies","Mistakes","Blunders","Book","Forced"])
        table.setToolTip(MOVE_QUALITY_TOOLTIP)
        table.verticalHeader().hide();table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection);table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);table.horizontalHeader().setStretchLastSection(True)
        for row,side in enumerate(("White","Black")):
            moves=[item for item in items if item.side==side]
            accuracy=review_accuracy(moves)
            counts={tag:sum(1 for item in moves if item.verdict==tag) for tag in ("Brilliant","Great","Best","Excellent","Good","Miss","Inaccuracy","Mistake","Blunder","Forced")}
            book=sum(1 for item in moves if item.book_status.startswith("Book move"))
            cells=[side,f"{accuracy:.1f}%" if accuracy is not None else "—",str(counts["Brilliant"]),str(counts["Great"]),str(counts["Best"]),str(counts["Excellent"]),str(counts["Good"]),str(counts["Miss"]),str(counts["Inaccuracy"]),str(counts["Mistake"]),str(counts["Blunder"]),str(book),str(counts["Forced"])]
            for col,value in enumerate(cells):table.setItem(row,col,QTableWidgetItem(value))
        layout.addWidget(table)
        narrative=QLabel("Game story: "+review_game_narrative(items,user_color));narrative.setObjectName("ReviewSummary");narrative.setWordWrap(True);layout.addWidget(narrative)
        coach=QLabel("Game-specific lessons: "+game_coach_summary(items,user_color));coach.setObjectName("ReviewSummary");coach.setWordWrap(True);layout.addWidget(coach)
        opening_items=[item for item in items if item.book_status.startswith("Book move")]
        opening_item=(max(opening_items,key=lambda item:item.ply) if opening_items else
                      next((item for item in items if item.ply==opening_ply),None))
        turning=rank_review_moments(items,limit=7 if opening_item else 8)
        if opening_item and all(item.ply!=opening_item.ply for item in turning):
            turning.append(opening_item);turning.sort(key=lambda item:item.ply)
        moment_tags=review_moment_tags(items,limit=7 if opening_item else 8)
        if opening_item:
            opening_tag=("Last book move" if opening_item.book_status.startswith("Book move")
                         else "Last documented opening move")
            opening_labels=moment_tags.setdefault(opening_item.ply,[])
            if opening_tag not in opening_labels:opening_labels.append(opening_tag)
        highlights=[]
        for item in turning[:8]:
            tags=" · ".join(moment_tags.get(item.ply,[item.verdict]))
            impact=review_moment_impact(item)
            highlights.append(f"{review_move_notation(item)} ({item.side}): {tags}"+
                              (f" ({impact})" if impact else ""))
        summary=QLabel("Key moments: "+(" · ".join(highlights) if highlights else "No major turning points flagged."));summary.setObjectName("ReviewSummary");summary.setWordWrap(True);layout.addWidget(summary)
        if turning:
            moments_layout=QGridLayout();moments_layout.setHorizontalSpacing(8);moments_layout.setVerticalSpacing(8)
            for index,item in enumerate(turning[:8]):
                tags=" · ".join(moment_tags.get(item.ply,[item.verdict]));impact=review_moment_impact(item)
                button=QPushButton(f"{review_move_notation(item)} · {tags}"+(f" · {impact}" if impact else ""))
                reason=review_text_for_viewer(item.explanation.strip(),item,user_color) or f"Stockfish's preferred move was {item.best_san}."
                impact_note=f" · {item.expected_loss*100:.1f} pp expected score lost" if impact else ""
                button.setToolTip(f"{item.side} to move{impact_note}\nAnalysis: {getattr(item,'confidence','Standard analysis')}\n{reason}\nOpen this move in the guided review")
                button.clicked.connect(lambda _checked=False,ply=item.ply:self._open_moment(ply))
                moments_layout.addWidget(button,index//4,index%4)
            layout.addLayout(moments_layout)
        if not any(item.book_status!="Book not loaded" for item in items):
            note=QLabel("Book moves need a weighted Polyglot .bin file. Lichess TSVs identify openings but don’t include move weights.");note.setObjectName("Muted");layout.addWidget(note)
        row=QHBoxLayout();row.addStretch()
        if turning:
            guided_button=QPushButton("Start with key moments")
            guided_button.setObjectName("PrimaryButton")
            guided_button.setToolTip("Begin with the first critical move; use the key-moment controls to continue through the game.")
            guided_button.clicked.connect(lambda _checked=False,ply=turning[0].ply:self._open_moment(ply))
            row.addWidget(guided_button)
            self.start_button=QPushButton("Review every move")
            self.start_button.setToolTip("Open the full move-by-move review from the beginning of the game.")
        else:
            self.start_button=QPushButton("Start Review")
            self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.accept);row.addWidget(self.start_button);root.addLayout(row)

    def _open_moment(self,ply):
        self.selected_ply=ply;self.accept()

__all__ = ['MistakePracticeDialog', 'PuzzleDialog', 'ReviewProgressDialog', 'ReviewSummaryDialog']
