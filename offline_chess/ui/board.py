"""Chess board, evaluation bar, and opening explorer widgets."""
from __future__ import annotations

import time
import sys

import chess
import chess.engine
import chess.polyglot
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QSpinBox,
    QAbstractItemView, QSizePolicy, QLineEdit,
)

from ..openings import *
from ..review_logic import *
from .common import fit_dialog_to_screen
from .theme import *

class EvalBar(QWidget):
    def __init__(self):
        super().__init__()
        self.cp = 0
        self.setMinimumWidth(44)
        self.setMaximumWidth(54)
    def set_eval(self, cp): self.cp = cp; self.update()
    def paintEvent(self, _event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect(); p.fillRect(r, QColor("#262b2f"))
        bounded = max(-900, min(900, self.cp)); white_share = .5 + bounded / 1800
        white_h = int(r.height() * white_share)
        p.fillRect(0, r.height() - white_h, r.width(), white_h, QColor("#e5e3dc"))
        p.setPen(QColor("#f4f5f6") if white_share < .5 else QColor("#273039"))
        p.setFont(QFont("Arial", 8, QFont.Weight.Bold)); p.drawText(r.adjusted(0, 5, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, eval_text(self.cp))


class ChessBoard(QWidget):
    square_clicked = pyqtSignal(int)
    default_theme = "classic_green"
    default_piece_style = "tournament"
    default_show_coordinates = True
    default_animations = True
    def __init__(self):
        super().__init__()
        self.board = chess.Board(); self.last_move = None; self.selected = None; self.best_move = None;self.review_sticker=None;self.sticker_color="#4baf73";self.focus_squares=()
        self.theme_key=ChessBoard.default_theme
        self.piece_style=ChessBoard.default_piece_style
        self.show_coordinates=ChessBoard.default_show_coordinates
        self.animations_enabled=ChessBoard.default_animations
        self.flipped = False
        self._flash_ticks=0
        self._flash_timer=QTimer(self);self._flash_timer.setInterval(16);self._flash_timer.timeout.connect(self._advance_motion)
        self.setMinimumSize(500, 500); self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    def set_position(self, board, last_move=None, selected=None, best_move=None,review_sticker=None,sticker_color="#4baf73",focus_squares=()):
        changed_move=last_move is not None and last_move!=self.last_move
        self.board, self.last_move, self.selected, self.best_move = board, last_move, selected, best_move
        self.review_sticker,self.sticker_color=review_sticker,sticker_color
        self.focus_squares=tuple(dict.fromkeys(square for square in focus_squares if square is not None))
        if changed_move and self.animations_enabled:
            self._flash_ticks=10;self._flash_timer.start()
        self.update()
    def set_theme(self,theme_key):
        if theme_key in BOARD_THEMES:self.theme_key=theme_key;self.update()
    def set_presentation(self,piece_style=None,show_coordinates=None,animations=None):
        if piece_style in PIECE_STYLES:self.piece_style=piece_style
        if show_coordinates is not None:self.show_coordinates=bool(show_coordinates)
        if animations is not None:
            self.animations_enabled=bool(animations)
            if not self.animations_enabled:self._flash_timer.stop();self._flash_ticks=0
        self.update()
    def _advance_motion(self):
        self._flash_ticks-=1
        if self._flash_ticks<=0:self._flash_ticks=0;self._flash_timer.stop()
        self.update()
    def _sq_rect(self, square, size):
        f, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (7-f, rank) if self.flipped else (f, 7-rank)
        return col*size, row*size, size, size
    def paintEvent(self, _event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()); unit = side/8
        ox, oy = (self.width()-side)/2, (self.height()-side)/2
        theme=BOARD_THEMES.get(self.theme_key,BOARD_THEMES["classic_green"])
        light, dark = QColor(theme["light"]), QColor(theme["dark"])
        p.fillRect(self.rect(),QColor("#16191b"))
        for row in range(8):
            for col in range(8):
                x,y=ox+col*unit,oy+row*unit
                sq = chess.square(7-col if self.flipped else col, row if self.flipped else 7-row)
                base = light if (row+col)%2==0 else dark
                if self.last_move and sq in (self.last_move.from_square, self.last_move.to_square): base = QColor(theme["last"])
                if sq == self.selected: base = QColor(theme["selected"])
                p.fillRect(int(x),int(y),int(unit+1),int(unit+1),base)
                if self._flash_ticks and self.last_move and sq==self.last_move.to_square:
                    flash=QColor("#ffffff");flash.setAlpha(16+self._flash_ticks*9);p.fillRect(int(x),int(y),int(unit+1),int(unit+1),flash)
                if sq in self.focus_squares:
                    p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor("#f1c75b"),max(2,int(unit*.035))))
                    inset=unit*.09;p.drawRoundedRect(QRectF(x+inset,y+inset,unit-2*inset,unit-2*inset),unit*.1,unit*.1)
                if self.best_move and sq in (self.best_move.from_square,self.best_move.to_square):
                    tint=QColor(theme["accent"]);tint.setAlpha(105);p.fillRect(int(x),int(y),int(unit),int(unit),tint)
                piece=self.board.piece_at(sq)
                if piece:
                    piece_style=PIECE_STYLES.get(self.piece_style,PIECE_STYLES["tournament"])
                    font_role=piece_style["font"]
                    if font_role=="sans":font_name="Segoe UI" if sys.platform=="win32" else "DejaVu Sans"
                    else:font_name="Segoe UI Symbol" if sys.platform=="win32" else "DejaVu Sans"
                    weight=QFont.Weight.Bold if piece_style.get("weight")=="bold" else QFont.Weight.Normal
                    p.setFont(QFont(font_name,int(unit*piece_style["scale"]),weight))
                    if piece_style.get("glyph")=="letters":
                        symbol=PIECE_LETTERS[piece.piece_type]
                        if not piece.color:symbol=symbol.lower()
                    else:symbol=GLYPHS[piece.symbol()]
                    if piece_style.get("badge"):
                        badge=QRectF(x+unit*.19,y+unit*.19,unit*.62,unit*.62)
                        badge_color=QColor(theme["dark"] if piece.color else theme["light"])
                        badge_edge=QColor("#f7f6f0") if piece.color else QColor("#202426")
                        p.setBrush(badge_color);p.setPen(QPen(badge_edge,max(1,int(unit*.025))));p.drawEllipse(badge)
                        foreground=badge_edge
                    else:foreground=QColor("#f7f6f0") if piece.color else QColor("#202426")
                    shadow=QColor("#182022") if piece.color else QColor("#dce1dd")
                    shadow.setAlpha(175 if piece_style.get("shadow") else 95)
                    p.setPen(shadow);p.drawText(int(x+unit*.018),int(y+unit*.025),int(unit),int(unit),Qt.AlignmentFlag.AlignCenter,symbol)
                    p.setPen(foreground);p.drawText(int(x),int(y),int(unit),int(unit),Qt.AlignmentFlag.AlignCenter,symbol)
                if self.show_coordinates and col==0:
                    p.setFont(QFont("Arial",max(8,int(unit*.13)),QFont.Weight.Bold))
                    p.setPen(dark if (row+col)%2==0 else light)
                    rank_label=str(1+row) if self.flipped else str(8-row)
                    p.drawText(int(x+3),int(y+2),int(unit*.2),int(unit*.2),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop,rank_label)
                if self.show_coordinates and row==7:
                    p.setFont(QFont("Arial",max(8,int(unit*.13)),QFont.Weight.Bold))
                    p.setPen(dark if (row+col)%2==0 else light)
                    file_label=chr(ord('h')-col) if self.flipped else chr(ord('a')+col)
                    p.drawText(int(x),int(y+unit-unit*.22),int(unit-4),int(unit*.18),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignBottom,file_label)
        if self.selected is not None:
            for move in self.board.legal_moves:
                if move.from_square == self.selected:
                    col,row,w,_=self._sq_rect(move.to_square,unit); center=QColor(theme["accent"]);center.setAlpha(150)
                    p.setBrush(QBrush(center)); p.setPen(Qt.PenStyle.NoPen)
                    if self.board.piece_at(move.to_square): p.drawEllipse(int(ox+col+unit*.08),int(oy+row+unit*.08),int(unit*.84),int(unit*.84))
                    else: p.drawEllipse(int(ox+col+unit*.39),int(oy+row+unit*.39),int(unit*.22),int(unit*.22))
        if self.board.is_check():
            king=self.board.king(self.board.turn)
            if king is not None:
                col,row,w,h=self._sq_rect(king,unit); p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor("#e44848"),4))
                p.drawRect(int(ox+col+2),int(oy+row+2),int(w-4),int(h-4))
        if self.best_move and self.best_move in self.board.legal_moves:
            sx,sy,_,_=self._sq_rect(self.best_move.from_square,unit); tx,ty,_,_=self._sq_rect(self.best_move.to_square,unit)
            sx+=ox+unit/2; sy+=oy+unit/2; tx+=ox+unit/2; ty+=oy+unit/2
            arrow=QColor(theme["accent"]);arrow.setAlpha(220)
            p.setPen(QPen(arrow,max(4,int(unit*.09)),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(int(sx),int(sy),int(tx),int(ty))
            angle=math.atan2(ty-sy,tx-sx);head=max(8,unit*.18)
            p.setBrush(arrow);p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(tx,ty),QPointF(tx-head*math.cos(angle-.5),ty-head*math.sin(angle-.5)),QPointF(tx-head*math.cos(angle+.5),ty-head*math.sin(angle+.5))]))
        if self.review_sticker:
            badge_width=min(side*.36,175);badge=QRectF(ox+side-badge_width-8,oy+8,badge_width,28);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(self.sticker_color));p.drawRoundedRect(badge,8,8)
            p.setPen(QColor("#ffffff"));p.setFont(QFont("Segoe UI",9,QFont.Weight.Bold));p.drawText(badge,Qt.AlignmentFlag.AlignCenter,self.review_sticker.upper())
    def mousePressEvent(self,event):
        side=min(self.width(),self.height()); unit=side/8; ox=(self.width()-side)/2; oy=(self.height()-side)/2
        col=int((event.position().x()-ox)//unit); row=int((event.position().y()-oy)//unit)
        if 0<=col<8 and 0<=row<8:
            sq=chess.square(7-col if self.flipped else col,row if self.flipped else 7-row)
            self.square_clicked.emit(sq)


class OpeningExplorer(QDialog):
    def __init__(self, board, book_path, request_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opening & Response Explorer"); fit_dialog_to_screen(self, parent, 1600, 1050)
        self.position=board.copy(); self.start=board.copy(); self.line=[]; self.moves=[]
        self.book_path=book_path; self.request_callback=request_callback
        layout=QVBoxLayout(self)
        self.heading=QLabel("Explore candidate moves and replies"); self.heading.setObjectName("PlayerName"); layout.addWidget(self.heading)
        self.path_label=QLabel("Starting position"); self.path_label.setObjectName("Muted"); layout.addWidget(self.path_label)
        screen=parent.screen() if parent is not None else QApplication.primaryScreen();preview_side=max(380,min(560,int(screen.availableGeometry().height()*.52))) if screen else 500
        center=QHBoxLayout(); self.preview=ChessBoard(); self.preview.setMinimumSize(preview_side,preview_side); center.addWidget(self.preview,1)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(["Move","Book weight","Eval (White)","Engine line"])
        self.table.verticalHeader().hide(); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.setAlternatingRowColors(True); self.table.setShowGrid(False)
        header=self.table.horizontalHeader(); header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_:self.play_selected()); center.addWidget(self.table,2); layout.addLayout(center,1)
        row=QHBoxLayout(); self.hint=QLabel("Book weights are relative move frequencies when the selected book provides them."); self.hint.setObjectName("Muted"); row.addWidget(self.hint,1)
        self.back_btn=QPushButton("Back"); self.back_btn.clicked.connect(self.back); row.addWidget(self.back_btn)
        self.reset_btn=QPushButton("Reset line"); self.reset_btn.clicked.connect(self.reset_line); row.addWidget(self.reset_btn)
        self.play_btn=QPushButton("Play move"); self.play_btn.setObjectName("PrimaryButton"); self.play_btn.clicked.connect(self.play_selected); row.addWidget(self.play_btn)
        layout.addLayout(row)
        self.request_lines()

    def request_lines(self):
        self.heading.setText(f"{('White' if self.position.turn else 'Black')} to move · requesting lines…")
        self.request_callback((self.position.copy(),self.book_path,6))

    def show_lines(self,payload):
        fen,rows=payload
        if fen!=self.position.fen():return
        self.preview.set_position(self.position,self.moves[-1] if self.moves else None)
        self.heading.setText(f"{('White' if self.position.turn else 'Black')} to move · {len(rows)} candidate moves")
        self.path_label.setText("Line: " + (" ".join(self.line) if self.line else "starting position"))
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            values=[row["san"],str(row["weight"]) if row["weight"] else "—",eval_text(row["eval"]) if row["eval"] is not None else "—",row["pv"] or "Book response"]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value)
                if col==0:cell.setData(Qt.ItemDataRole.UserRole,row["move"])
                self.table.setItem(i,col,cell)
        if rows:self.table.selectRow(0)
        if self.book_path:
            self.hint.setText("Book weights show relative frequency; engine scores are shown when available.")
        else:
            self.hint.setText("No weighted book loaded. Showing Stockfish variations; load a Polyglot .bin book for book frequencies.")

    def play_selected(self):
        row=self.table.currentRow()
        if row<0:return
        item=self.table.item(row,0); move=item.data(Qt.ItemDataRole.UserRole) if item else None
        if move not in self.position.legal_moves:return
        self.line.append(self.position.san(move)); self.moves.append(move); self.position.push(move); self.request_lines()

    def back(self):
        if not self.line:return
        self.line.pop(); self.moves.pop(); self.position=self.start.copy()
        for move in self.moves:self.position.push(move)
        self.preview.set_position(self.position,self.moves[-1] if self.moves else None)
        self.request_lines()

    def reset_line(self):
        self.position=self.start.copy(); self.line=[]; self.moves=[]; self.preview.set_position(self.position); self.request_lines()

__all__ = ['EvalBar', 'ChessBoard', 'OpeningExplorer']
