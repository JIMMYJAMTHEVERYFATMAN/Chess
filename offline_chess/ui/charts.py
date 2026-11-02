"""Charts for review quality, Chess.com profile trends, and move navigation."""
from __future__ import annotations

import math
import re

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QToolTip, QWidget

from ..review_logic import *

class AccuracyChart(QWidget):
    def __init__(self,values=None,parent=None):
        super().__init__(parent); self.values=values or []; self.setMinimumHeight(180);self.setToolTip(MOVE_QUALITY_TOOLTIP)
    def set_values(self,values):self.values=values;self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(42,14,-14,-28);p.setFont(QFont("Arial",9));p.setPen(QColor("#8f999e"))
        for value in (0,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(2,int(y-8),34,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Move quality appears after your first reviewed game.");return
        pts=[];n=len(self.values)
        for i,value in enumerate(self.values):
            x=area.left()+(area.width()*(i/(n-1) if n>1 else .5));bounded=max(0,min(100,float(value)))
            y=area.bottom()-(bounded/100)*area.height();pts.append(QPointF(x,y))
        p.setPen(QPen(QColor("#88c66a"),3));
        for a,b in zip(pts,pts[1:]):p.drawLine(a,b)
        for point in pts:p.setPen(QPen(QColor("#b5e393"),2));p.setBrush(QColor("#82bd60"));p.drawEllipse(point,4,4)


class EloHistoryChart(QWidget):
    def __init__(self,values=None,parent=None):
        super().__init__(parent);self.values=list(values or []);self.setMinimumHeight(170);self.setMouseTracking(True);self.setToolTip("Chess.com rating after each downloaded game, in date order.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(54,14,-16,-28);p.setFont(QFont("Arial",9));p.setPen(QColor("#8f999e"))
        if not self.values:
            p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Download rated games to build your Chess.com rating history.");return
        ratings=[int(item[1]) for item in self.values];low=(min(ratings)//100)*100;high=((max(ratings)+99)//100)*100
        if high<=low:low-=100;high+=100
        else:low=max(0,low-50);high+=50
        for value in range(low,high+1,max(100,math.ceil((high-low)/4/100)*100)):
            y=area.bottom()-(value-low)/(high-low)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),48,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,str(value))
        points=[]
        for index,(_,rating) in enumerate(self.values):
            x=area.left()+area.width()*(index/(len(self.values)-1) if len(self.values)>1 else .5)
            y=area.bottom()-(rating-low)/(high-low)*area.height();points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#80c7db"),2.5))
        for a,b in zip(points,points[1:]):p.drawLine(a,b)
        for point in points:p.setPen(QPen(QColor("#d3f1f7"),1));p.setBrush(QColor("#80c7db"));p.drawEllipse(point,3.5,3.5)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+7,100,18,Qt.AlignmentFlag.AlignLeft, str(self.values[0][0])[:10])
        p.drawText(area.right()-100,area.bottom()+7,100,18,Qt.AlignmentFlag.AlignRight,str(self.values[-1][0])[:10])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(54,14,-16,-28)
        fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        index=round(fraction*(len(self.values)-1));date,rating=self.values[index]
        QToolTip.showText(event.globalPosition().toPoint(),f"{date} · {rating} Chess.com rating",self)
    def leaveEvent(self,event):
        QToolTip.hideText();super().leaveEvent(event)


class ChessComResultsChart(QWidget):
    """Compact monthly results chart for downloaded Chess.com games."""
    COLORS={"wins":QColor("#80c7db"),"draws":QColor("#d6b86a"),"losses":QColor("#df8278")}
    def __init__(self,parent=None):
        super().__init__(parent);self.months=[];self.setMinimumHeight(170);self.setMouseTracking(True)
        self.setToolTip("Monthly win, draw, and loss proportions for the selected time control.")
    def set_values(self,months):self.months=list(months or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,14,-14,-30);p.setFont(QFont("Arial",9))
        if not self.months:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Download completed games to see monthly results.");return
        legend_x=area.left()
        for label,key in (("Wins","wins"),("Draws","draws"),("Losses","losses")):
            p.fillRect(QRectF(legend_x,2,9,9),self.COLORS[key]);p.setPen(QColor("#b3bcc0"));p.drawText(int(legend_x+13),0,48,13,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,label)
            legend_x+=62
        for value in (0,25,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        count=len(self.months);step=area.width()/count;bar_width=max(8,min(34,step*.58))
        for index,item in enumerate(self.months):
            month,wins,draws,losses=item;total=wins+draws+losses
            if not total:continue
            x=area.left()+step*(index+.5);bottom=area.bottom()
            for amount,key in ((losses,"losses"),(draws,"draws"),(wins,"wins")):
                height=area.height()*amount/total
                if height>0:p.fillRect(QRectF(x-bar_width/2,bottom-height,bar_width,height),self.COLORS[key])
                bottom-=height
            p.setPen(QColor("#929ca0"))
            if count<=6 or index in (0,count-1) or index%max(1,count//5)==0:
                p.drawText(int(x-step/2),area.bottom()+7,int(step),18,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,month[2:])
            if count<=12:p.drawText(int(x-step/2),area.top()-1,int(step),14,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,str(total))
    def mouseMoveEvent(self,event):
        if not self.months:return
        area=self.rect().adjusted(48,14,-14,-30);fraction=max(0,min(.999,(event.position().x()-area.left())/max(1,area.width())))
        month,wins,draws,losses=self.months[min(len(self.months)-1,int(fraction*len(self.months)))];total=wins+draws+losses
        score=(wins+.5*draws)/total*100 if total else 0
        QToolTip.showText(event.globalPosition().toPoint(),f"{month} · {wins} wins / {draws} draws / {losses} losses · {score:.0f}% score",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComAccuracyChart(QWidget):
    """Accuracy trend indexed either by month or by the player's move number."""
    def __init__(self,description="accuracy",parent=None):
        super().__init__(parent);self.values=[];self.description=description;self.setMinimumHeight(160);self.setMouseTracking(True)
        source="Chess.com archive" if "Chess.com" in description else "local reviewed games"
        self.setToolTip(f"Average {source} {description}; hover a point for its sample size.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,14,-16,-28);p.setFont(QFont("Arial",9))
        for value in (0,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        if not self.values:
            message=("No Chess.com accuracy data in the downloaded games."
                     if "Chess.com" in self.description else "Current game reviews will build this accuracy trend.")
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,message);return
        points=[];n=len(self.values)
        for index,(_label,value,_count) in enumerate(self.values):
            x=area.left()+area.width()*(index/(n-1) if n>1 else .5);y=area.bottom()-(max(0,min(100,float(value)))/100)*area.height()
            points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#a8d47a"),2.5))
        for a,b in zip(points,points[1:]):p.drawLine(a,b)
        for point in points:p.setBrush(QColor("#a8d47a"));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3.5,3.5)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+6,120,18,Qt.AlignmentFlag.AlignLeft,self.values[0][0])
        p.drawText(area.right()-120,area.bottom()+6,120,18,Qt.AlignmentFlag.AlignRight,self.values[-1][0])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(48,14,-16,-28);fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        label,value,count=self.values[round(fraction*(len(self.values)-1))]
        QToolTip.showText(event.globalPosition().toPoint(),f"{label} · {value:.1f}% average · {count} games",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComShapeChart(QWidget):
    """Average user-side expected-score trace for a selected local game-flow pattern."""
    def __init__(self,parent=None):
        super().__init__(parent);self.profiles={};self.pattern="";self.setMinimumHeight(190);self.setMouseTracking(True)
        self.setToolTip("Average expected score for reviewed games in the selected local flow pattern.")
    def set_profiles(self,profiles):
        self.profiles=dict(profiles or {})
        if self.pattern not in self.profiles:
            self.pattern=max(self.profiles,key=lambda name:self.profiles[name][1],default="")
        self.update()
    def set_pattern(self,pattern):self.pattern=str(pattern or "");self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,18,-18,-32);p.setFont(QFont("Arial",9))
        if not self.pattern or self.pattern not in self.profiles:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Review games to build local game-flow traces.");return
        values,count=self.profiles[self.pattern]
        for score in (0,25,50,75,100):
            y=area.bottom()-(score/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{score}%")
        points=[]
        for index,value in enumerate(values):
            x=area.left()+area.width()*(index/max(1,len(values)-1));y=area.bottom()-(max(0,min(100,float(value)*100))/100)*area.height()
            points.append(QPointF(x,y))
        p.setPen(QPen(QColor("#88c66a"),2.5))
        for left,right in zip(points,points[1:]):p.drawLine(left,right)
        for point in points:p.setBrush(QColor("#88c66a"));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3,3)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+7,100,18,Qt.AlignmentFlag.AlignLeft,"Game start")
        p.drawText(area.center().x()-45,area.bottom()+7,90,18,Qt.AlignmentFlag.AlignHCenter,"Midpoint")
        p.drawText(area.right()-100,area.bottom()+7,100,18,Qt.AlignmentFlag.AlignRight,"Game end")
    def mouseMoveEvent(self,event):
        if not self.pattern or self.pattern not in self.profiles:return
        values,count=self.profiles[self.pattern];area=self.rect().adjusted(48,18,-18,-32)
        fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        index=round(fraction*(len(values)-1));progress=round(fraction*100)
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"{self.pattern} · {progress}% through game · {values[index]*100:.1f}% expected score · {count} games",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class ChessComQualityTrendChart(QWidget):
    """Monthly shares of locally reviewed moves by quality band."""
    SERIES=(("Brilliant / Great","#b68ae0"),("Best","#45b9d0"),
            ("Excellent / Good","#8bbf65"),("Errors / Misses","#e68a47"),
            ("Book / Forced","#64b5d6"))
    def __init__(self,parent=None):
        super().__init__(parent);self.values=[];self.setMinimumHeight(190);self.setMouseTracking(True)
        self.setToolTip("Monthly share of your locally reviewed moves in each quality band.")
    def set_values(self,values):self.values=list(values or []);self.update()
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(48,48,-16,-28);p.setFont(QFont("Arial",9))
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Current game reviews will build this move-quality trend.");return
        cell_width=area.width()/3
        for index,(label,color) in enumerate(self.SERIES):
            legend_x=area.left()+(index%3)*cell_width;legend_y=14+(index//3)*18
            p.setPen(QColor(color));p.setBrush(QColor(color));p.drawEllipse(QPointF(legend_x+4,legend_y),4,4)
            p.setPen(QColor("#b3bcc0"));p.drawText(int(legend_x+12),int(legend_y-8),max(32,int(cell_width-16)),17,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,label)
        for value in (0,25,50,75,100):
            y=area.bottom()-(value/100)*area.height();p.setPen(QPen(QColor("#373e41"),1));p.drawLine(area.left(),int(y),area.right(),int(y))
            p.setPen(QColor("#929ca0"));p.drawText(0,int(y-8),40,16,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f"{value}%")
        for series_index,(_label,color) in enumerate(self.SERIES):
            points=[]
            for index,(_month,shares,_total) in enumerate(self.values):
                x=area.left()+area.width()*(index/max(1,len(self.values)-1))
                y=area.bottom()-(max(0,min(100,float(shares[series_index])))/100)*area.height()
                points.append(QPointF(x,y))
            p.setPen(QPen(QColor(color),2))
            for left,right in zip(points,points[1:]):p.drawLine(left,right)
            for point in points:p.setBrush(QColor(color));p.setPen(QPen(QColor("#e3f2cf"),1));p.drawEllipse(point,3,3)
        p.setPen(QColor("#929ca0"));p.drawText(area.left(),area.bottom()+6,120,18,Qt.AlignmentFlag.AlignLeft,self.values[0][0])
        p.drawText(area.right()-120,area.bottom()+6,120,18,Qt.AlignmentFlag.AlignRight,self.values[-1][0])
    def mouseMoveEvent(self,event):
        if not self.values:return
        area=self.rect().adjusted(48,48,-16,-28);fraction=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        month,shares,total=self.values[round(fraction*(len(self.values)-1))]
        details=" · ".join(f"{label}: {shares[index]:.1f}%" for index,(label,_color) in enumerate(self.SERIES))
        QToolTip.showText(event.globalPosition().toPoint(),f"{month} · {total:,} reviewed moves · {details}",self)
    def leaveEvent(self,event):QToolTip.hideText();super().leaveEvent(event)


class MomentumChart(QWidget):
    move_selected=pyqtSignal(int)
    def __init__(self,parent=None,player_color=None):
        super().__init__(parent);self.player_color=player_color;self.values=[];self.moves=[];self.selected_ply=0;self._last_tooltip="";self.setMouseTracking(True);self.setMinimumHeight(106);self.setMaximumHeight(130);self.setToolTip("Colored dots mark key move classifications. Hover for details; click to jump to a move.")
    def set_values(self,values,selected_ply=0,moves=None):
        self.values=values;self.selected_ply=selected_ply;self.moves=list(moves or ());self._last_tooltip="";self.update()
    def _ply_at_x(self,x):
        area=self.rect().adjusted(42,8,-10,-19)
        fraction=max(0,min(1,(x-area.left())/max(1,area.width())))
        return round(fraction*(len(self.values)-1))+1
    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor("#202426"))
        area=self.rect().adjusted(42,8,-10,-19);p.setFont(QFont("Arial",8))
        if not self.values:
            p.setPen(QColor("#98a1a5"));p.drawText(area,Qt.AlignmentFlag.AlignCenter,"Momentum appears after the game review.");return
        mid=area.top()+area.height()/2;p.setPen(QPen(QColor("#41494c"),1,Qt.PenStyle.DashLine));p.drawLine(area.left(),int(mid),area.right(),int(mid))
        p.setPen(QColor("#8f999e"));p.drawText(2,area.top()+9,38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"White +");p.drawText(2,int(mid+5),38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"Even");p.drawText(2,area.bottom(),38,14,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,"Black +")
        count=len(self.values);points=[]
        for i,value in enumerate(self.values):
            cp=max(-1000,min(1000,value));x=area.left()+area.width()*(i/(count-1) if count>1 else .5);y=mid-(cp/2000)*area.height()
            points.append(QPointF(x,y))
        for i,(a,b) in enumerate(zip(points,points[1:])):
            avg=(self.values[i]+self.values[i+1])/2
            p.setPen(QPen(QColor("#82bd60" if avg>=0 else "#d17969"),2.4));p.drawLine(a,b)
        marker_colors={"Brilliant":"#b68ae0","Great":"#45b9d0","Miss":"#e68a47",
                       "Inaccuracy":"#e0b746","Mistake":"#e68a47","Blunder":"#e05252"}
        for index,item in enumerate(self.moves[:len(points)]):
            marker=marker_colors.get(item.verdict)
            if marker:
                p.setPen(QPen(QColor("#202426"),1));p.setBrush(QColor(marker));p.drawEllipse(points[index],3.5,3.5)
        if 1<=self.selected_ply<=len(points):
            point=points[self.selected_ply-1];p.setPen(QPen(QColor("#f2cb58"),1,Qt.PenStyle.DashLine));p.drawLine(int(point.x()),area.top(),int(point.x()),area.bottom())
            p.setPen(QPen(QColor("#f2cb58"),1));p.setBrush(QColor("#f2cb58"));p.drawEllipse(point,4,4)
    def mousePressEvent(self,event):
        if not self.values:return
        self.move_selected.emit(self._ply_at_x(event.position().x()))
    def mouseMoveEvent(self,event):
        if not self.values or not self.moves:
            self._last_tooltip="";return
        index=min(self._ply_at_x(event.position().x())-1,len(self.moves)-1)
        item=self.moves[index]
        parts=[f"{review_move_notation(item)} · {item.side}",
               f"Evaluation: {evaluation_label(item.eval_after,item.mate_after)}",
               f"{item.verdict} · {item.expected_loss*100:.1f} pp expected score lost",
               f"Engine choice: {item.best_san}",
               f"Analysis: {item.confidence}"]
        if item.best_san!=item.san and item.best_reason:
            parts.append("Why: "+review_text_for_viewer(item.best_reason,item,self.player_color))
        detail=review_text_for_viewer(item.explanation.strip(),item,self.player_color)
        if detail:parts.append(detail)
        tooltip="\n".join(parts)
        if tooltip!=self._last_tooltip:
            QToolTip.showText(event.globalPosition().toPoint(),tooltip,self)
            self._last_tooltip=tooltip
    def leaveEvent(self,event):
        self._last_tooltip="";QToolTip.hideText();super().leaveEvent(event)

__all__ = ['AccuracyChart', 'EloHistoryChart', 'ChessComResultsChart', 'ChessComAccuracyChart', 'ChessComShapeChart', 'ChessComQualityTrendChart', 'MomentumChart']
