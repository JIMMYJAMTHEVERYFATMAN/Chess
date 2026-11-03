"""Chess review models, scoring rules, annotations, and coaching logic."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Optional

import chess
import chess.engine
import chess.pgn

MIN_ELO, MAX_ELO = 1320, 3190
NEAR_BEST_EXPECTED_POINTS_TOLERANCE = .01
BRILLIANT_MAX_PREMOVE_EXPECTED_POINTS = .98
BRILLIANT_MIN_POSTMOVE_EXPECTED_POINTS = .55
BRILLIANT_MAX_EXPECTED_POINTS_LOSS = .02
BRILLIANT_MIN_REPORTED_GAP = .04
BRILLIANT_MIN_UNIQUENESS_GAP = .05
REVIEW_ANALYSIS_VERSION = 13
ACCURACY_CALIBRATION_EXPONENT = 5.0
ACCURACY_MODEL_VERSION = "caps2-inspired-v1"
GREAT_ONLY_MAX_PREMOVE_EXPECTED_POINTS = .75
GREAT_ONLY_MIN_POSTMOVE_EXPECTED_POINTS = .50
GREAT_ONLY_MIN_ALTERNATIVE_GAP = .15
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def eval_cp(score: chess.engine.PovScore) -> int:
    score = score.white()
    if score.is_mate():
        m = score.mate()
        return 100_000 if m is None or m > 0 else -100_000
    return int(score.score(mate_score=100_000) or 0)


def engine_line_forces_mate(score: chess.engine.PovScore, color: chess.Color) -> bool:
    """True only when the score says this side can force mate, not receive it."""
    distance=score.pov(color).mate()
    return distance is not None and distance>0


def eval_text(cp: int) -> str:
    if abs(cp) >= 99_000:
        return "+M" if cp > 0 else "−M"
    return f"{cp / 100:+.2f}"


def evaluation_label(cp: int, mate: Optional[int] = None) -> str:
    if mate is None:return eval_text(cp)
    if mate>0:return f"+M{mate}"
    if mate<0:return f"−M{abs(mate)}"
    return "M0"


def expected_points(cp_white: int) -> float:
    """Smoothly map a White-POV centipawn score to an expected game score."""
    bounded=max(-4000,min(4000,int(cp_white)))
    return 1.0/(1.0+math.exp(-bounded/500.0))


MOVE_QUALITY_TOOLTIP=("Local accuracy is a CAPS2-inspired grade based on Stockfish's expected-score loss. "
                      "The nonlinear grade scale gives small errors a visible cost, so it is not a literal "
                      "percentage of expected score retained and is not Chess.com's exact CAPS2 formula.")
REVIEW_IMPACT_TOOLTIP=("Impact compares the played move with Stockfish's top line. "
                       "Expected-score loss is shown in percentage points (pp); the centipawn gap is separate.")


def raw_move_accuracy(loss: int = 0, ep_loss: Optional[float] = None) -> float:
    """Uncalibrated expected-score retention used as input to the local accuracy grade."""
    if ep_loss is not None:
        return max(0.0,min(100.0,100.0*(1.0-max(0.0,min(1.0,ep_loss)))))
    return max(0.0,min(100.0,100.0*math.exp(-.0035*min(1500,max(0,loss)))))


def calibrate_accuracy(raw_score: float) -> float:
    """Compress the high end of expected-score retention into a more useful grade scale."""
    bounded=max(0.0,min(100.0,float(raw_score)))
    return 100.0*(bounded/100.0)**ACCURACY_CALIBRATION_EXPONENT


def uncalibrate_accuracy(grade: float) -> float:
    bounded=max(0.0,min(100.0,float(grade)))
    return 100.0*(bounded/100.0)**(1.0/ACCURACY_CALIBRATION_EXPONENT)


def move_accuracy(loss: int = 0, ep_loss: Optional[float] = None) -> float:
    """Per-move CAPS2-inspired quality grade, with mate-safe fallback."""
    return calibrate_accuracy(raw_move_accuracy(loss,ep_loss))


def review_accuracy(moves) -> Optional[float]:
    """Calibrate a game's mean raw move retention once, avoiding top-heavy raw averages."""
    if not moves:return None
    raw_values=[]
    for move in moves:
        grade=getattr(move,"accuracy",None)
        raw_values.append(uncalibrate_accuracy(grade) if grade is not None else
                          raw_move_accuracy(getattr(move,"loss",0),getattr(move,"expected_loss",None)))
    return calibrate_accuracy(sum(raw_values)/len(raw_values))


def format_review_impact(expected_loss: float, cp_loss: int) -> str:
    return f"{expected_loss*100:.1f} pp expected score lost · {cp_loss} cp"


def count_label(count: int, singular: str, plural: Optional[str] = None) -> str:
    """Format a counted noun without exposing template-style '(s)' copy."""
    count=int(count)
    noun=singular if abs(count)==1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def normalized_move_loss(cp_before: int, cp_after: int, mover: chess.Color,
                        mate_before: Optional[int] = None, mate_after: Optional[int] = None) -> tuple[int,float]:
    """Return centipawn and expected-points loss from the mover's perspective."""
    sign=1 if mover==chess.WHITE else -1
    cp_loss=max(0,(int(cp_before)-int(cp_after))*sign)
    before_white=expected_points(cp_before);after_white=expected_points(cp_after)
    before_mover=before_white if mover==chess.WHITE else 1-before_white
    after_mover=after_white if mover==chess.WHITE else 1-after_white
    ep_loss=max(0.0,before_mover-after_mover)
    if mate_before is not None and mate_after is not None:
        sign=1 if mover==chess.WHITE else -1
        before_mate=mate_before*sign;after_mate=mate_after*sign
        delayed=0
        if before_mate>0 and after_mate>before_mate:delayed=after_mate-before_mate
        elif before_mate<0 and after_mate<0 and abs(after_mate)<abs(before_mate):delayed=abs(before_mate)-abs(after_mate)
        if delayed:
            cp_loss=max(cp_loss,delayed*120)
            ep_loss=max(ep_loss,min(.35,delayed*.025))
    return cp_loss,ep_loss


def override_review_category(verdict: str, color: str, legal_count: int, book_move: bool) -> tuple[str,str]:
    if legal_count<=1:return "Forced","#8aa6c1"
    if book_move:return "Book","#64b5d6"
    return verdict,color


def classify(loss: int, ep_loss: Optional[float] = None) -> tuple[str, str]:
    """Classify by expected-score change; centipawn thresholds are legacy fallback."""
    if ep_loss is None:
        tag="Best" if loss<10 else "Excellent" if loss<30 else "Good" if loss<70 else "Inaccuracy" if loss<130 else "Mistake" if loss<250 else "Blunder"
    else:
        tag="Best" if ep_loss<.005 else "Excellent" if ep_loss<.02 else "Good" if ep_loss<.05 else "Inaccuracy" if ep_loss<.10 else "Mistake" if ep_loss<.20 else "Blunder"
    colors={"Best":"#39a982","Excellent":"#4baf73","Good":"#8bbf65","Inaccuracy":"#e0b746","Mistake":"#e68a47","Blunder":"#e05252","Great":"#45b9d0","Brilliant":"#b68ae0","Miss":"#e68a47","Book":"#64b5d6","Forced":"#8aa6c1"}
    return tag,colors[tag]


def review_verdict_tooltip(item: ReviewMove) -> str:
    """Explain the visible move label and the confidence behind it."""
    definitions={
        "Brilliant":"A sound piece sacrifice that deep analysis confirms as the unique best choice.",
        "Great":"A verified turnaround or a move that stands far above the other choices.",
        "Best":"Stockfish's top choice in this position.",
        "Excellent":"Very close to the top choice; expected score loss is below 2 percentage points.",
        "Good":"A reasonable move; expected score loss is below 5 percentage points.",
        "Inaccuracy":"A small but meaningful drop in expected score (5–10 percentage points).",
        "Mistake":"A significant drop in expected score (10–20 percentage points).",
        "Blunder":"A major drop in expected score (20 percentage points or more).",
        "Miss":"A clear winning chance or forced mate was available and went unclaimed.",
        "Book":"This move appears in the loaded opening book.",
        "Forced":"This was the only legal move.",
    }
    description=definitions.get(item.verdict,"Engine classification based on the analyzed position.")
    impact=f"Expected score lost vs Stockfish's top line: {item.expected_loss*100:.1f} percentage points (pp)."
    return f"{description}\n{impact}\nAnalysis: {item.confidence}."


def near_best_moves(board: chess.Board, lines: list[dict], mover: chess.Color,
                   tolerance: float = NEAR_BEST_EXPECTED_POINTS_TOLERANCE) -> tuple[chess.Move, ...]:
    """Return tied MultiPV choices, respecting forced-mate outcomes and distance."""
    scored=[]
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        mover_ep=white_ep if mover==chess.WHITE else 1-white_ep
        scored.append((pv[0],mover_ep,score.pov(mover).mate()))
    if not scored:return ()
    winning_mates=[mate for _move,_value,mate in scored if mate is not None and mate>0]
    if winning_mates:
        shortest=min(winning_mates)
        return tuple(dict.fromkeys(move for move,_value,mate in scored if mate==shortest))
    non_mates=[value for _move,value,mate in scored if mate is None]
    if non_mates:
        best_ep=max(non_mates)
        return tuple(dict.fromkeys(move for move,value,mate in scored
                                   if mate is None and best_ep-value<=tolerance))
    losing_mates=[abs(mate) for _move,_value,mate in scored if mate is not None and mate<0]
    if losing_mates:
        slowest=max(losing_mates)
        return tuple(dict.fromkeys(move for move,_value,mate in scored if mate==-slowest))
    best_ep=max(value for _move,value,_mate in scored)
    return tuple(dict.fromkeys(move for move,value,_mate in scored if best_ep-value<=tolerance))


@dataclass(frozen=True)
class ReviewEvent:
    kind: str
    text: str
    severity: str = "info"
    prompt: str = ""
    focus_squares: tuple[chess.Square,...] = ()


@dataclass
class ReviewMove:
    ply: int
    san: str
    best_san: str
    verdict: str
    color: str
    loss: int
    eval_after: int
    pv: str
    explanation: str
    side: str
    book_status: str
    book_weight: int
    phase: str
    accuracy: float
    best_move: chess.Move
    expected_loss: float = 0.0
    confidence: str = "Standard analysis"
    theme: str = ""
    eval_before: int = 0
    opponent_response: str = ""
    opponent_pv: str = ""
    best_pv_moves: tuple[chess.Move, ...] = ()
    events: tuple[ReviewEvent, ...] = ()
    best_reason: str = ""
    mate_before: Optional[int] = None
    mate_after: Optional[int] = None
    opponent_move: Optional[chess.Move] = None
    acceptable_moves: tuple[chess.Move, ...] = ()
    alternative_sans: tuple[str, ...] = ()
    move_number: int = 0
    opponent_pv_moves: tuple[chess.Move, ...] = ()


TACTICAL_REVIEW_EVENT_KINDS=frozenset({"fork","skewer","discovered_attack","discovered_check",
                                      "removed_defender","new_pin","opponent_hanging_piece","promotion","sound_sacrifice",
                                      "pv_material_gain"})
TACTICAL_REVIEW_VERDICTS=frozenset({"Best","Excellent","Good","Great","Brilliant"})


def has_review_tactical_idea(item: ReviewMove) -> bool:
    return item.verdict in TACTICAL_REVIEW_VERDICTS and any(
        event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive"
        for event in getattr(item,"events",()))


def normalized_syzygy_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path))) if path else ""


def serialize_review_moves(items: list[ReviewMove], syzygy_path: str = "") -> str:
    """Encode an engine review for the optional local full-game archive."""
    payload=[]
    for item in items:
        row=dict(item.__dict__)
        for key in ("best_move","opponent_move"):
            row[key]=row[key].uci() if row[key] else None
        for key in ("best_pv_moves","acceptable_moves","opponent_pv_moves"):
            row[key]=[move.uci() for move in row[key]]
        row["events"]=[event.__dict__ | {"focus_squares":list(event.focus_squares)} for event in row["events"]]
        payload.append(row)
    return json.dumps({"version":REVIEW_ANALYSIS_VERSION,"syzygy_path":normalized_syzygy_path(syzygy_path),
                       "moves":payload},separators=(",",":"))


def deserialize_review_moves(value: str, syzygy_path: str = "") -> list[ReviewMove]:
    """Decode a cached review, rejecting malformed or incompatible payloads."""
    saved=json.loads(value)
    if not isinstance(saved,dict) or saved.get("version")!=REVIEW_ANALYSIS_VERSION:
        raise ValueError("Saved review was produced by older analysis rules")
    if saved.get("syzygy_path","")!=normalized_syzygy_path(syzygy_path):
        raise ValueError("Saved review used different Syzygy tablebases")
    rows=saved.get("moves")
    if not isinstance(rows,list):raise ValueError("Saved review is not a list")
    items=[]
    for row in rows:
        row=dict(row)
        for key in ("best_move","opponent_move"):
            row[key]=chess.Move.from_uci(row[key]) if row.get(key) else None
        for key in ("best_pv_moves","acceptable_moves","opponent_pv_moves"):
            row[key]=tuple(chess.Move.from_uci(move) for move in row.get(key,[]))
        row["alternative_sans"]=tuple(row.get("alternative_sans",()))
        row["events"]=tuple(ReviewEvent(**(event | {"focus_squares":tuple(event.get("focus_squares",()))})) for event in row.get("events",[]))
        items.append(ReviewMove(**row))
    return items


def review_move_notation(item: ReviewMove) -> str:
    """Format a reviewed ply as standard chess move notation, including Black's ellipsis."""
    number=getattr(item,"move_number",0) or (item.ply+1)//2
    marker="." if item.side=="White" else "..."
    return f"{number}{marker} {item.san}"


def review_text_for_viewer(text: str, item: ReviewMove,
                           user_color: Optional[chess.Color]) -> str:
    """Name the mover explicitly when coaching text describes the opponent's move."""
    if not text or user_color is None:
        return text
    mover_color=chess.WHITE if item.side=="White" else chess.BLACK
    if mover_color==user_color:return text
    side=item.side
    replacements=(("worse for you",f"worse for {side}"),("better for you",f"better for {side}"),
                  ("in favor of you",f"in favor of {side}"),("ahead of you",f"ahead of {side}"),
                  ("to you",f"to {side}"),("for you",f"for {side}"),
                  ("against you",f"against {side}"),("from you",f"from {side}"),
                  ("with you",f"with {side}"),("You keep",f"{side} keeps"),("You preserve",f"{side} preserves"),
                  ("You give",f"{side} gives"),("You have",f"{side} has"),
                  ("You are",f"{side} is"),("You were",f"{side} was"),
                  ("You find",f"{side} finds"),("You missed",f"{side} missed"),
                  ("You found",f"{side} found"),("you keep",f"{side} keeps"),
                  ("you preserve",f"{side} preserves"),("you give",f"{side} gives"),
                  ("you have",f"{side} has"),("you are",f"{side} is"),
                  ("you were",f"{side} was"),("you find",f"{side} finds"),
                  ("you missed",f"{side} missed"),("you found",f"{side} found"),
                  ("Your",f"{side}'s"),("your",f"{side}'s"),("You",side),("you",side))
    for source,target in replacements:
        text=re.sub(rf"\b{re.escape(source)}\b",target,text)
    return text


def parse_review_san_line(board: chess.Board, san_line: str) -> tuple[chess.Move, ...]:
    """Rebuild a saved reply line from SAN when loading reviews archived before UCI lines were stored."""
    position=board.copy(stack=False);moves=[]
    for san in san_line.split():
        try:move=position.parse_san(san)
        except ValueError:break
        moves.append(move);position.push(move)
    return tuple(moves)


def review_spoken_text(item: ReviewMove, user_color: Optional[chess.Color] = None) -> str:
    """Build a concise spoken version of the selected move's coaching detail."""
    explanation=review_text_for_viewer(item.explanation,item,user_color)
    parts=[f"{review_move_notation(item)}, {item.verdict.lower()}.",explanation]
    if user_color is not None:
        is_user_move=(item.side=="White")== (user_color==chess.WHITE)
        owner="your" if is_user_move else "your opponent's"
        parts.insert(1,f"That was {owner} move as {item.side}.")
    if item.best_san!=item.san and item.best_reason:
        parts.append("Engine comparison: "+review_text_for_viewer(item.best_reason,item,user_color))
    if item.alternative_sans:
        parts.append("Other near-equal choices: "+", ".join(item.alternative_sans)+".")
    if item.opponent_response:
        parts.append("Strongest reply: "+item.opponent_response+".")
    prompt=review_text_for_viewer(review_reflection_prompt(item.events) or "",item,user_color)
    if prompt:parts.append("Think it through: "+prompt)
    return " ".join(part.strip() for part in parts if part and part.strip())


def review_game_narrative(items: list[ReviewMove],
                         user_color: Optional[chess.Color] = None) -> str:
    """Lead with the defining moment, then add a separate major swing if useful."""
    if not items:return "No reviewed moves are available yet."
    final=items[-1]
    largest=max(items,key=lambda item:item.expected_loss)
    voice_seed=sum(ord(char) for item in items for char in item.san)+sum(item.ply for item in items)

    def swing_story(item):
        notation=review_move_notation(item);alternative=getattr(item,"best_san","")
        top_choice=f"Stockfish's top choice, {alternative}" if alternative else "the engine's preferred continuation"
        points=item.expected_loss*100
        unit="percentage point" if round(points,1)==1.0 else "percentage points"
        if item.verdict=="Miss":
            templates=(
                f"The clearest missed chance was {notation} by {item.side}: it cost {points:.1f} {unit} of expected score compared with {top_choice}.",
                f"On {notation}, {item.side} missed a chance. Stockfish estimates {alternative or 'the top line'} kept {points:.1f} {unit} more expected score.",
                f"Stockfish found a stronger opportunity at {notation}: {item.side}'s move gave up {points:.1f} {unit} against {top_choice}.",
                f"The missed opportunity came on {notation}. The engine rates {top_choice} {points:.1f} {unit} higher in expected score than the played move.")
        else:
            templates=(
                f"The biggest swing came on {notation} by {item.side}: the played move cost {points:.1f} {unit} of expected score compared with {top_choice}.",
                f"On {notation}, {item.side}'s move left {points:.1f} {unit} of expected score on the table; Stockfish preferred {alternative or 'another continuation'}.",
                f"Stockfish's largest disagreement with the game was {notation}: it rates {alternative or 'its top line'} {points:.1f} {unit} ahead of the move played.",
                f"The position shifted most after {notation}. The engine's top line held {points:.1f} {unit} more expected score than {item.side}'s move.")
        return templates[voice_seed%len(templates)]

    story="";story_ply=None
    if any(event.kind=="checkmate" and event.severity=="positive"
           for event in getattr(final,"events",())):
        story=f"The game ended in checkmate on {review_move_notation(final)} by {final.side}."
        story_ply=final.ply
    elif any(event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"}
             for event in getattr(final,"events",())):
        ending=next(event for event in final.events
                    if event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"})
        story=f"The game ended after {review_move_notation(final)} by {final.side}. {ending.text}"
        story_ply=final.ply
    standout=next((item for item in items if item.verdict=="Brilliant"
                   and any(event.kind=="sound_sacrifice" and event.severity=="positive" for event in item.events)),None)
    if not story and standout:
        event=next(event for event in standout.events if event.kind=="sound_sacrifice" and event.severity=="positive")
        event_text=review_text_for_viewer(event.text,standout,user_color)
        story=f"Standout: {review_move_notation(standout)} by {standout.side} was a confirmed sacrifice. {event_text}"
        story_ply=standout.ply
    great=max((item for item in items if item.verdict=="Great"),
              key=lambda item:item.expected_loss,default=None)
    if great and (not story or story_ply==final.ply):
        only_move=next((event for event in great.events if event.kind=="only_good_move"),None)
        turnaround=next((event for event in great.events if event.kind=="turnaround"),None)
        if turnaround:
            highlight=f"Turning point: {review_move_notation(great)} by {great.side}. {turnaround.text}"
        elif only_move:
            highlight=f"Standout resource: {review_move_notation(great)} was the only move that held at least an equal position. {only_move.text}"
        else:
            highlight=(f"Turning point: {review_move_notation(great)} brought {great.side} back from a difficult position; "
                       "the engine's expected-score estimate improved from below 25% to at least 50%.")
        story=(story+" "+highlight).strip()
        story_ply=great.ply
    tactical_items=[item for item in items if has_review_tactical_idea(item)]
    tactic=max(tactical_items,key=lambda item:(sum(event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive"
                                                    for event in item.events),
                                               item.expected_loss,-item.ply),default=None)
    if not story and tactic:
        event=next(event for event in tactic.events if event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive")
        event_text=review_text_for_viewer(event.text,tactic,user_color)
        story=f"Tactical highlight: {review_move_notation(tactic)} by {tactic.side}. {event_text}"
        story_ply=tactic.ply
    if not story and largest.expected_loss>=.05:
        return swing_story(largest)
    if not story:
        steady_stories=(f"No single move changed much: the largest gap from Stockfish's top choice was "
                        f"{largest.expected_loss*100:.1f} percentage points, on {review_move_notation(largest)}.",
                        f"The move-by-move choices stayed close to Stockfish's top lines; the largest gap was "
                        f"{largest.expected_loss*100:.1f} percentage points on {review_move_notation(largest)}.",
                        f"The closest call was {review_move_notation(largest)} by {largest.side}: it was only "
                        f"{largest.expected_loss*100:.1f} percentage points from the top choice.")
        story=steady_stories[voice_seed%len(steady_stories)]
        story_ply=largest.ply
    last_book_item=None;first_book_exit=None
    for item in items:
        book_status=getattr(item,"book_status","")
        if book_status.startswith("Book move"):
            last_book_item=item
        elif last_book_item and book_status=="Out of book":
            first_book_exit=item;break
    if first_book_exit:
        opening_note=(f"Opening checkpoint: {review_move_notation(first_book_exit)} by {first_book_exit.side} "
                      f"was the first move outside the loaded book after {review_move_notation(last_book_item)}. "
                      f"Stockfish rated it {first_book_exit.verdict}.")
        impact=review_moment_impact(first_book_exit)
        if impact:opening_note+=f" It gave up {impact} against the top line."
        story+=" "+opening_note
    if largest.expected_loss>=.05 and largest.ply!=story_ply:
        story+=" "+swing_story(largest)
    return story


def game_phase(board: chess.Board) -> str:
    non_pawn_material=sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.piece_type!=chess.PAWN and p.piece_type!=chess.KING)
    return "Opening" if board.fullmove_number<=12 else "Endgame" if non_pawn_material<=13 else "Middlegame"


def castling_timing_label(phase,ply):
    if phase=="Opening":
        try:move_number=max(1,(int(ply)+1)//2)
        except (TypeError,ValueError):return "Opening · move unknown"
        if move_number<3:return "Opening · moves 1–2"
        if move_number<=6:return f"Opening · move {move_number}"
        return "Opening · move 7+"
    return phase if phase in ("Middlegame","Endgame") else "Did not castle"


def describe_move(before: chess.Board, move: chess.Move, after: chess.Board,
                  loss: int, best_san: str, material_delta: int, verdict: str = "Good",
                  theme: str = "") -> str:
    who = "White" if before.turn else "Black"
    facts = []
    if before.is_capture(move):
        victim = before.piece_at(move.to_square)
        if before.is_en_passant(move): facts.append("captures en passant")
        elif victim: facts.append(f"wins a {chess.piece_name(victim.piece_type)}")
    if move.promotion: facts.append(f"promotes to a {chess.piece_name(move.promotion)}")
    if before.is_castling(move): facts.append("castles to improve king safety")
    if after.is_checkmate(): facts.append("delivers checkmate")
    elif after.is_check(): facts.append("gives check")
    if material_delta:
        side_gain = material_delta if before.turn == chess.WHITE else -material_delta
        if side_gain > 0: facts.append(f"comes out about {count_label(side_gain,'material point')} ahead")
        elif side_gain < 0: facts.append(f"ends up about {count_label(abs(side_gain),'material point')} down")
    if verdict == "Miss": facts.append(f"misses a chance to play {best_san}, which would have created a clearly better position")
    elif verdict == "Blunder": facts.append(f"{best_san} was the best defence; this move gives up about {loss/100:.1f} pawns of engine evaluation")
    elif verdict == "Mistake": facts.append(f"{best_san} was stronger; the move gives up about {loss/100:.1f} pawns of evaluation")
    elif verdict == "Inaccuracy": facts.append(f"{best_san} keeps more of the position's value (about {loss/100:.1f} pawns)")
    elif verdict in ("Best","Brilliant"):
        facts.append("matches or closely matches the engine's top choice")
    elif verdict=="Great":
        facts.append("finds a resourceful way to improve a difficult position")
    if theme: facts.append(theme)
    if not facts: facts.append("keeps the position close to its previous evaluation")
    return who + " " + "; ".join(facts) + "."


def material_balance(board: chess.Board) -> int:
    score = 0
    for piece_type, value in VALUES.items():
        score += value * (len(board.pieces(piece_type, chess.WHITE)) - len(board.pieces(piece_type, chess.BLACK)))
    return score


def captured_piece_details(board: chess.Board, move: chess.Move) -> tuple[Optional[chess.Piece],Optional[chess.Square]]:
    """Return the captured piece and its square, including en-passant captures."""
    if move not in board.legal_moves or not board.is_capture(move):return None,None
    square=move.to_square;victim=board.piece_at(square)
    if board.is_en_passant(move):
        square=move.to_square-8 if board.turn==chess.WHITE else move.to_square+8
        victim=chess.Piece(chess.PAWN,not board.turn)
    return victim,square


def legal_capture_map(board: chess.Board, attacker_color: chess.Color) -> dict[chess.Square,set[chess.Square]]:
    """Map occupied targets to pieces that can legally capture them."""
    probe=board.copy(stack=False);probe.turn=attacker_color
    captures={}
    for move in probe.legal_moves:
        if probe.is_capture(move) and probe.piece_at(move.to_square) is not None:
            captures.setdefault(move.to_square,set()).add(move.from_square)
    return captures


def legal_defender_map(board: chess.Board, defender_color: chess.Color, include_pawns=False,
                       include_king=False) -> dict[chess.Square,set[chess.Square]]:
    """Map friendly pieces to legal captures they could make on their square."""
    defenders={}
    for square,piece in board.piece_map().items():
        if (piece.color!=defender_color or (piece.piece_type==chess.PAWN and not include_pawns)
                or (piece.piece_type==chess.KING and not include_king)):continue
        probe=board.copy(stack=False)
        probe.remove_piece_at(square)
        probe.set_piece_at(square,chess.Piece(chess.PAWN,not defender_color))
        probe.turn=defender_color
        for move in probe.legal_moves:
            if move.to_square==square and probe.is_capture(move):
                defenders.setdefault(square,set()).add(move.from_square)
    return defenders


def skewer_lines(board: chess.Board, attacker_color: chess.Color) -> list[tuple[chess.Piece,chess.Square,chess.Piece,chess.Square,chess.Piece,chess.Square]]:
    """Find slider lines attacking a more valuable front piece and one behind it."""
    lines=[];target_color=not attacker_color
    for source,piece in board.piece_map().items():
        if piece.color!=attacker_color or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        directions=[]
        if piece.piece_type in (chess.BISHOP,chess.QUEEN):directions.extend(((-1,-1),(-1,1),(1,-1),(1,1)))
        if piece.piece_type in (chess.ROOK,chess.QUEEN):directions.extend(((-1,0),(1,0),(0,-1),(0,1)))
        for df,dr in directions:
            file,rank=chess.square_file(source)+df,chess.square_rank(source)+dr;found=[]
            while 0<=file<8 and 0<=rank<8:
                square=chess.square(file,rank);target=board.piece_at(square)
                if target:
                    if target.color==target_color:found.append((target,square))
                    if len(found)>=2 or target.color==attacker_color:break
                file+=df;rank+=dr
            if (len(found)>=2 and VALUES.get(found[0][0].piece_type,0)>VALUES.get(found[1][0].piece_type,0)
                    and found[0][0].piece_type in (chess.QUEEN,chess.ROOK,chess.KING)):
                lines.append((piece,source,found[0][0],found[0][1],found[1][0],found[1][1]))
    return lines


def newly_created_skewers(before: chess.Board, after: chess.Board,
                          attacker_color: chess.Color):
    """Return only skewers that were absent before the move."""
    def key(line):
        pinner,source,front,front_square,rear,rear_square=line
        return (source,pinner.piece_type,front_square,front.piece_type,
                rear_square,rear.piece_type)
    old={key(line) for line in skewer_lines(before,attacker_color)}
    return [line for line in skewer_lines(after,attacker_color) if key(line) not in old]


def fork_targets(board: chess.Board, move: chess.Move) -> list[tuple[chess.Piece,chess.Square]]:
    """Return valuable targets the moved piece can legally attack after the move."""
    if move not in board.legal_moves:return []
    after=board.copy(stack=False);after.push(move);mover=board.turn
    moved_piece=after.piece_at(move.to_square);targets=[]
    if moved_piece:
        probe=after.copy(stack=False);probe.turn=mover
        legal_capture_squares={candidate.to_square for candidate in probe.legal_moves
                               if candidate.from_square==move.to_square}
        checking_squares=set(after.checkers())
        for square in after.attacks(move.to_square):
            piece=after.piece_at(square)
            legal_attack=(move.to_square in checking_squares if piece and piece.piece_type==chess.KING
                          else square in legal_capture_squares)
            if (piece and piece.color!=mover and legal_attack
                    and (piece.piece_type==chess.KING or VALUES.get(piece.piece_type,0)>=3)):
                targets.append((piece,square))
    return targets


def tactical_themes(board: chess.Board, move: chess.Move) -> set[str]:
    """Recognize a few concrete tactical motifs to label locally generated puzzles."""
    if move not in board.legal_moves:return set()
    mover=board.turn;opponent=not mover;after=board.copy();after.push(move);themes=set()
    if after.is_checkmate():themes.add("Mate")
    elif after.is_check():themes.add("Check")
    if move.promotion:themes.add("Promotion")
    victim,capture_square=captured_piece_details(board,move)
    attacker=board.piece_at(move.from_square)
    if victim and victim.color!=mover:
        victim_value=VALUES.get(victim.piece_type,0);attacker_value=VALUES.get(attacker.piece_type,0) if attacker else 0
        defended=bool(board.attackers(not mover,capture_square))
        if victim_value>=5 or victim_value>=attacker_value+2 or not defended:themes.add("Winning material")
    targets=[piece.piece_type for piece,_ in fork_targets(board,move)]
    if len(targets)>=2 and any(target in (chess.KING,chess.QUEEN,chess.ROOK) for target in targets):themes.add("Fork")
    if (any(piece.color==opponent for _pinner,_source,piece,_square in newly_pinned_pieces(board,after))
            or newly_relative_pins(board,after,mover)):
        themes.add("Pin")
    for source,piece in board.piece_map().items():
        if piece.color!=mover or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN) or source==move.from_square:continue
        newly_attacked=set(after.attacks(source))-set(board.attacks(source))
        for square in newly_attacked:
            target=after.piece_at(square)
            if target is not None and target.color!=mover and target.piece_type in (chess.KING,chess.QUEEN,chess.ROOK):
                themes.add("Discovered attack")
                if target.piece_type==chess.KING:themes.add("Discovered check")
                break
        if "Discovered attack" in themes:break
    # Keep puzzle labels and review explanations grounded in the same line test.
    if newly_created_skewers(board,after,mover):themes.add("Skewer")
    return themes


def newly_opened_slider_targets(before: chess.Board, after: chess.Board,
                                moving_square: chess.Square) -> list[tuple[chess.Piece,chess.Square,chess.Piece,chess.Square]]:
    """Return enemy king, queen, or rook squares newly attacked by an unmoved slider."""
    mover=before.turn;opened=[]
    for source,piece in after.piece_map().items():
        if piece.color!=mover or piece.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        if source==moving_square:continue
        new_attacks=set(after.attacks(source))-set(before.attacks(source))
        for target_square in new_attacks:
            target=after.piece_at(target_square)
            if target and target.color!=mover and target.piece_type in (chess.KING,chess.QUEEN,chess.ROOK):
                opened.append((piece,source,target,target_square))
    return sorted(opened,key=lambda item:(-VALUES.get(item[2].piece_type,0),item[1],item[3]))


def newly_pinned_pieces(before: chess.Board, after: chess.Board):
    """Return newly absolutely pinned pieces with the pinning slider and squares."""
    pins=[]
    for square,piece in after.piece_map().items():
        if piece.piece_type==chess.KING:continue
        color=piece.color
        if not after.is_pinned(color,square) or before.is_pinned(color,square):continue
        pin_ray=after.pin(color,square)
        pinners=[sq for sq in after.attackers(not color,square)
                 if sq in pin_ray and after.piece_at(sq) and after.piece_at(sq).piece_type in (chess.BISHOP,chess.ROOK,chess.QUEEN)]
        if pinners:pins.append((after.piece_at(pinners[0]),pinners[0],piece,square))
    return pins


def relative_pin_lines(board: chess.Board, attacker_color: chess.Color):
    """Find line pieces that attack an enemy piece in front of its rook or queen."""
    lines=[]
    orthogonal=((1,0),(-1,0),(0,1),(0,-1))
    diagonal=((1,1),(1,-1),(-1,1),(-1,-1))
    for source,pinner in board.piece_map().items():
        if pinner.color!=attacker_color or pinner.piece_type not in (chess.BISHOP,chess.ROOK,chess.QUEEN):continue
        directions=(orthogonal if pinner.piece_type==chess.ROOK else diagonal if pinner.piece_type==chess.BISHOP else orthogonal+diagonal)
        source_file,source_rank=chess.square_file(source),chess.square_rank(source)
        for df,dr in directions:
            first=None
            for distance in range(1,8):
                file,rank=source_file+df*distance,source_rank+dr*distance
                if not (0<=file<8 and 0<=rank<8):break
                square=chess.square(file,rank);piece=board.piece_at(square)
                if piece is None:continue
                if first is None:
                    if piece.color==attacker_color:break
                    first=(piece,square)
                    continue
                front,front_square=first
                if (piece.color!=attacker_color and front.piece_type!=chess.KING
                        and piece.piece_type in (chess.QUEEN,chess.ROOK)
                        and VALUES[piece.piece_type]>VALUES[front.piece_type]):
                    lines.append((pinner,source,front,front_square,piece,square))
                break
    return lines


def newly_relative_pins(before: chess.Board, after: chess.Board, attacker_color: chess.Color):
    old={(source,pinner.piece_type,front_square,target_square,target.piece_type)
         for pinner,source,_front,front_square,target,target_square in relative_pin_lines(before,attacker_color)}
    return [line for line in relative_pin_lines(after,attacker_color)
            if (line[1],line[0].piece_type,line[3],line[5],line[4].piece_type) not in old]


def piece_label(piece: Optional[chess.Piece]) -> str:
    return chess.piece_name(piece.piece_type) if piece else "piece"


def _king_pressure(board: chess.Board, color: chess.Color) -> int:
    """Count squares in the king's zone controlled by the enemy."""
    king=board.king(color)
    if king is None:return 0
    file,rank=chess.square_file(king),chess.square_rank(king)
    zone={king}
    for df in (-1,0,1):
        for dr in (-1,0,1):
            f,r=file+df,rank+dr
            if 0<=f<8 and 0<=r<8:zone.add(chess.square(f,r))
    return sum(bool(board.attackers(not color,square)) for square in zone)


def _controlled_center_squares(board: chess.Board, color: chess.Color) -> set[chess.Square]:
    center=(chess.D4,chess.E4,chess.D5,chess.E5)
    return {square for square in center if board.attackers(color,square)}


def _center_control(board: chess.Board, color: chess.Color) -> int:
    return len(_controlled_center_squares(board,color))


def missed_plan_event(before: chess.Board, played_move: chess.Move, played_after: chess.Board,
                      best_move: chess.Move) -> Optional[ReviewEvent]:
    """Explain a quiet engine alternative only when a concrete positional change is measurable."""
    if best_move not in before.legal_moves:return None
    mover=before.turn;best_after=before.copy(stack=False);best_after.push(best_move)
    best_san=before.san(best_move);played_piece=before.piece_at(best_move.from_square)

    new_center=_controlled_center_squares(best_after,mover)-_controlled_center_squares(played_after,mover)
    if new_center:
        squares=", ".join(chess.square_name(square) for square in sorted(new_center))
        noun="square" if len(new_center)==1 else "squares"
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} adds attacks on the central {noun} {squares} compared with your move.","warning",
                           f"The alternative {best_san} attacks {squares} in the center. Which of those squares matters most for your next plan?",tuple(new_center))

    pressure_after=_king_pressure(played_after,mover);pressure_best=_king_pressure(best_after,mover)
    if pressure_after-pressure_best>=2:
        king=best_after.king(mover)
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} reduces enemy attacks around your king on {chess.square_name(king)} from {pressure_after} to {pressure_best} nearby squares.","warning",
                           f"The alternative {best_san} lowers the pressure around your king. Which line or escape square becomes safer?",(king,))

    if (before.fullmove_number<=12 and played_piece and played_piece.piece_type in (chess.KNIGHT,chess.BISHOP)
            and chess.square_rank(best_move.from_square) in (0,7)
            and chess.square_rank(best_move.to_square) not in (0,7)):
        return ReviewEvent("missed_plan",f"The engine's alternative {best_san} develops your {piece_label(played_piece)} from {chess.square_name(best_move.from_square)} to {chess.square_name(best_move.to_square)} instead of leaving it on its starting square.","warning",
                           f"The alternative {best_san} develops your {piece_label(played_piece)}. What useful squares does it reach from {chess.square_name(best_move.to_square)}?",(best_move.from_square,best_move.to_square))

    if played_piece and played_move.from_square==best_move.from_square:
        actual_piece=played_after.piece_at(played_move.to_square)
        best_count=_piece_mobility(best_after,mover,best_move.to_square)
        actual_count=_piece_mobility(played_after,mover,played_move.to_square)
        if actual_piece and actual_piece.piece_type==played_piece.piece_type and best_count>=actual_count+4:
            return ReviewEvent("missed_plan",f"The engine's alternative {best_san} gives your {piece_label(played_piece)} {best_count} legal moves, compared with {actual_count} after your move.","warning",
                               f"The alternative {best_san} gives your {piece_label(played_piece)} more routes. Which route improves its role in the position?",(played_move.to_square,best_move.to_square))
    return None


def removed_defender_event(before: chess.Board, best_move: chess.Move,
                           best_pv: tuple[chess.Move,...], missed: bool = True) -> Optional[ReviewEvent]:
    """Describe a defender capture only when the engine line later wins its target."""
    if best_move not in before.legal_moves or not before.is_capture(best_move):return None
    defender,defender_square=captured_piece_details(before,best_move)
    if defender is None or defender.piece_type==chess.KING:return None
    mover=before.turn;opponent=not mover
    after_best=before.copy(stack=False);after_best.push(best_move)
    for target_square,target in before.piece_map().items():
        if target.color!=opponent or target.piece_type not in (chess.QUEEN,chess.ROOK):continue
        old_defenders=set(before.attackers(opponent,target_square))
        new_defenders=set(after_best.attackers(opponent,target_square))
        removed=old_defenders-new_defenders
        if (best_move.to_square not in removed or not old_defenders
                or len(new_defenders)>=len(old_defenders)
                or not after_best.attackers(mover,target_square)):
            continue
        line=before.copy(stack=False);tracked_square=target_square;line_sans=[];won_target=False
        sequence=best_pv if best_pv and best_pv[0]==best_move else (best_move,)+best_pv
        for line_move in sequence:
            if line_move not in line.legal_moves:break
            line_sans.append(line.san(line_move))
            victim,capture_square=captured_piece_details(line,line_move)
            if line.turn==mover and victim==target and capture_square==tracked_square:
                won_target=True;break
            if line_move.from_square==tracked_square:tracked_square=line_move.to_square
            line.push(line_move)
        if not won_target:continue
        defender_word="the only defender" if not new_defenders else "a defender"
        san_line=" ".join(line_sans)
        prompt=(f"Which capture removes {defender_word} of the {piece_label(target)} on "
                f"{chess.square_name(target_square)}, and how does the engine line win it?")
        lead=f"The best move {before.san(best_move)}" if missed else f"Your move {before.san(best_move)}"
        return ReviewEvent("missed_tactic" if missed else "removed_defender",
                           f"{lead} removes {defender_word} "
                           f"{piece_label(defender)} on {chess.square_name(defender_square)}, which protects "
                           f"the {piece_label(target)} on {chess.square_name(target_square)}. "
                           f"The engine line {san_line} wins the {piece_label(target)}.",
                           "warning" if missed else "positive",prompt,
                           (defender_square,target_square,best_move.from_square))
    return None


def _piece_mobility(board: chess.Board, color: chess.Color, square: chess.Square) -> int:
    probe=board.copy(stack=False);probe.turn=color
    return sum(1 for move in probe.legal_moves if move.from_square==square)


def _pawn_structure(board: chess.Board, color: chess.Color) -> tuple[set[int],set[int]]:
    files={f:[] for f in range(8)}
    for square in board.pieces(chess.PAWN,color):files[chess.square_file(square)].append(square)
    doubled={f for f,squares in files.items() if len(squares)>1}
    occupied={f for f,squares in files.items() if squares}
    isolated={f for f in occupied if f-1 not in occupied and f+1 not in occupied}
    return doubled,isolated


def _safe_piece_escapes(board: chess.Board, color: chess.Color, square: chess.Square) -> int:
    """Conservatively count legal exits to squares not attacked by the opponent."""
    probe=board.copy(stack=False);probe.turn=color
    safe=0
    for move in list(probe.legal_moves):
        if move.from_square!=square:continue
        after=probe.copy(stack=False);after.push(move)
        if not after.attackers(not color,move.to_square):safe+=1
    return safe


def _pv_material_swing(board: chess.Board, pv: tuple[chess.Move,...], color: chess.Color) -> int:
    initial=material_balance(board);line=board.copy(stack=False)
    for move in pv:
        if move not in line.legal_moves:break
        line.push(move)
    change=material_balance(line)-initial
    return change if color==chess.WHITE else -change


def _exchange_gain_on_square(board: chess.Board, square: chess.Square) -> int:
    """Estimate best material gain in a legal capture sequence on one square."""
    best=0
    for move in list(board.legal_moves):
        if move.to_square!=square or not board.is_capture(move):continue
        victim,_=captured_piece_details(board,move)
        if not victim:continue
        gain=VALUES.get(victim.piece_type,0)
        after=board.copy(stack=False);after.push(move)
        gain-=_exchange_gain_on_square(after,square)
        best=max(best,gain)
    return best


def _pv_captures_moved_piece(board: chess.Board, move: chess.Move,
                            pv: tuple[chess.Move,...]) -> bool:
    """Check whether the line actually captures the piece whose move is under review."""
    sequence=pv if pv and pv[0]==move else (move,)+pv
    line=board.copy(stack=False)
    if not sequence or sequence[0] not in line.legal_moves:return False
    tracked_square=sequence[0].to_square
    line.push(sequence[0])
    for reply in sequence[1:]:
        if reply not in line.legal_moves:break
        _victim,capture_square=captured_piece_details(line,reply)
        if capture_square==tracked_square:return True
        if reply.from_square==tracked_square:tracked_square=reply.to_square
        line.push(reply)
    return False


def is_engine_justified_sacrifice(before: chess.Board, move: chess.Move, best_move: chess.Move,
                                 pv: tuple[chess.Move,...], before_mover_ep: float,
                                 actual_mover_ep: float, forced_mate: bool = False) -> bool:
    if move not in before.legal_moves or move!=best_move:return False
    attacker=before.piece_at(move.from_square);victim,_=captured_piece_details(before,move)
    attacker_value=VALUES.get(attacker.piece_type,0) if attacker else 0
    victim_value=VALUES.get(victim.piece_type,0) if victim else 0
    is_sacrifice=attacker_value-victim_value>=2 and attacker_value>=3
    if (not is_sacrifice or before_mover_ep>=BRILLIANT_MAX_PREMOVE_EXPECTED_POINTS
            or actual_mover_ep<max(BRILLIANT_MIN_POSTMOVE_EXPECTED_POINTS,
                                   before_mover_ep-BRILLIANT_MAX_EXPECTED_POINTS_LOSS)):
        return False
    sequence=pv if pv and pv[0]==move else (move,)+pv
    if (_pv_captures_moved_piece(before,move,sequence)
            and _pv_material_swing(before,sequence,before.turn)<=-2):
        return True
    # If the engine declines the offer, confirm that accepting it really wins
    # material after the available recaptures. A merely capturable piece is not
    # enough: a rook trade or a poisoned queen capture is not a sacrifice.
    after=before.copy(stack=False);after.push(move)
    offered=after.piece_at(move.to_square)
    if not offered:return False
    offered_value=VALUES.get(offered.piece_type,0)
    for reply in list(after.legal_moves):
        if not after.is_capture(reply):continue
        captured,capture_square=captured_piece_details(after,reply)
        if captured!=offered or capture_square!=move.to_square:continue
        accepted=after.copy(stack=False);accepted.push(reply)
        if -offered_value+_exchange_gain_on_square(accepted,move.to_square)<=-2:
            return True
    return False


def brilliant_choice_gap(board: chess.Board, lines: list[dict], mover: chess.Color,
                         played_move: chess.Move,
                         minimum_gap: float = BRILLIANT_MIN_REPORTED_GAP) -> float:
    """Return the expected-points gap only when the played move is a clear engine first choice."""
    scored=[]
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        scored.append((pv[0],white_ep if mover==chess.WHITE else 1-white_ep))
    if len(scored)<2:return 0.0
    best_move,best_ep=max(scored,key=lambda item:item[1])
    if best_move!=played_move:return 0.0
    runner_up=max((ep for move,ep in scored if move!=played_move),default=best_ep)
    gap=max(0.0,best_ep-runner_up)
    return gap if gap>=minimum_gap else 0.0


def can_label_brilliant(justified: bool, budget: float, book_move: bool, legal_count: int,
                        unique_best: bool = False) -> bool:
    return bool(justified and unique_best and budget>=.4 and not book_move and legal_count>1)


def only_good_move_gap(board: chess.Board, lines: list[dict], mover: chess.Color,
                       played_move: chess.Move, legal_count: int,
                       before_mover_ep: float) -> float:
    """Return a gap only when every legal move is scored and one preserves equality."""
    if (before_mover_ep>=GREAT_ONLY_MAX_PREMOVE_EXPECTED_POINTS
            or len(lines)<legal_count or legal_count<=1):return 0.0
    scores={}
    for line in lines:
        pv=line.get("pv",[]);score=line.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        white_ep=expected_points(eval_cp(score))
        scores[pv[0]]=white_ep if mover==chess.WHITE else 1-white_ep
    if len(scores)!=legal_count:return 0.0
    best_move,best_ep=max(scores.items(),key=lambda item:item[1])
    if best_move!=played_move or best_ep<GREAT_ONLY_MIN_POSTMOVE_EXPECTED_POINTS:return 0.0
    runner_up=max(ep for move,ep in scores.items() if move!=played_move)
    gap=best_ep-runner_up
    return gap if gap>=GREAT_ONLY_MIN_ALTERNATIVE_GAP else 0.0


def analyze_review_events(before: chess.Board, move: chess.Move, after: chess.Board,
                          best_move: chess.Move, response: Optional[chess.Move] = None,
                          best_pv: tuple[chess.Move,...] = (), repeated_piece_moves: int = 0,
                          response_pv: tuple[chess.Move,...] = (),
                          explain_best_difference: bool = True) -> tuple[ReviewEvent,...]:
    """Build concrete, inspectable evidence from the played transition and engine lines."""
    events=[];mover=before.turn;opponent=not mover
    mover_piece=before.piece_at(move.from_square)
    move_san=before.san(move)
    response_san=after.san(response) if response and response in after.legal_moves else ""
    response_takes=response_san and after.is_capture(response)
    response_victim,response_capture_square=(captured_piece_details(after,response)
                                             if response_takes and response else (None,None))
    response_square=(chess.square_name(response_capture_square)
                     if response_capture_square is not None else "")
    if before.is_capture(move):
        victim,capture_square=captured_piece_details(before,move)
        if victim:events.append(ReviewEvent("capture",f"{move_san} captures the opponent's {piece_label(victim)} on {chess.square_name(capture_square)}.","positive",focus_squares=(move.to_square,)))
    if response_takes and response_victim:
        events.append(ReviewEvent("opponent_capture",f"Stockfish's strongest reply is {response_san}, capturing your {piece_label(response_victim)} on {response_square}.","critical",focus_squares=(response_capture_square,)))
    if response_san and after.gives_check(response):
        response_board=after.copy(stack=False);response_board.push(response)
        if response_board.is_checkmate():
            king=response_board.king(mover)
            if king is not None and chess.square_rank(king) in (0,7):
                events.append(ReviewEvent("back_rank_mate",f"Stockfish's strongest reply is {response_san}, delivering back-rank checkmate; your king on {chess.square_name(king)} has no escape.","critical"))
            else:events.append(ReviewEvent("opponent_mate",f"Stockfish's strongest reply is {response_san}, delivering checkmate.","critical"))
        else:events.append(ReviewEvent("opponent_check",f"Stockfish's strongest reply is {response_san}, giving check.","warning"))
    pv_swing=_pv_material_swing(after,response_pv[:8],mover) if response_pv else 0
    pv_loss=max(0,-pv_swing)
    if pv_loss>=2:
        events.append(ReviewEvent("pv_material_loss",f"In the engine's continuation after {response_san or 'the strongest reply'}, the material balance ends about {count_label(pv_loss,'point')} worse for you.","critical"))
    pv_gain=max(0,pv_swing)
    if pv_gain>=2:
        line=after.copy(stack=False);line_sans=[];focus=[]
        for line_move in response_pv[:8]:
            if line_move not in line.legal_moves:break
            if line.is_capture(line_move):
                _victim,capture_square=captured_piece_details(line,line_move)
                if capture_square is not None:focus.append(capture_square)
            line_sans.append(line.san(line_move));line.push(line_move)
        events.append(ReviewEvent("pv_material_gain",
                                  f"Stockfish's line {' '.join(line_sans)} ends about {count_label(pv_gain,'material point')} in your favor.",
                                  "positive","Trace the engine's line: which exchange leaves you ahead in material?",
                                  tuple(focus[:4])))

    # Detect an unchanged friendly piece that lost its defender and can now be
    # captured by the engine's actual best reply.
    old_defender_map=legal_defender_map(before,mover)
    new_defender_map=legal_defender_map(after,mover)
    legal_attacker_map=legal_capture_map(after,opponent)
    old_opponent_defender_map=legal_defender_map(before,opponent,include_pawns=True,include_king=True)
    new_opponent_defender_map=legal_defender_map(after,opponent,include_pawns=True,include_king=True)
    old_mover_capture_map=legal_capture_map(before,mover)
    mover_capture_map=legal_capture_map(after,mover)
    for square,piece in after.piece_map().items():
        if piece.color!=mover or piece.piece_type in (chess.PAWN,chess.KING) or square==move.to_square:continue
        old=before.piece_at(square)
        if old!=piece:continue
        old_defenders=old_defender_map.get(square,set())
        new_defenders=new_defender_map.get(square,set())
        attackers=legal_attacker_map.get(square,set())
        if not attackers:continue
        departed=old_defenders-new_defenders
        lost_only_defender=(len(old_defenders)==1 and len(new_defenders)==0 and move.from_square in departed)
        if lost_only_defender:
            if response_san and response_takes and response_capture_square==square:
                text=f"Moving your {piece_label(mover_piece)} from {chess.square_name(move.from_square)} removes the only defender of your {piece_label(piece)} on {chess.square_name(square)}; {response_san} can capture it."
            else:
                text=f"Moving your {piece_label(mover_piece)} from {chess.square_name(move.from_square)} leaves your {piece_label(piece)} on {chess.square_name(square)} attacked by {count_label(len(attackers),'piece')} and without a defender."
            events.append(ReviewEvent("lost_defender",text,"critical",focus_squares=(square,)));break
        if len(attackers)>len(new_defenders) and not old_defenders and response_san and response_takes and response_capture_square==square:
            events.append(ReviewEvent("hanging_piece",f"Your {piece_label(piece)} on {chess.square_name(square)} is left undefended; {response_san} captures it.","critical",focus_squares=(square,)));break
        if response_san and response_takes and response_capture_square==square and len(attackers)>len(new_defenders):
            events.append(ReviewEvent("hanging_piece",f"Your {piece_label(piece)} on {chess.square_name(square)} is attacked by {count_label(len(attackers),'piece')} and defended by {count_label(len(new_defenders),'piece')}; {response_san} captures it.","critical",focus_squares=(square,)));break

    for square,piece in after.piece_map().items():
        if piece.color!=opponent or piece.piece_type in (chess.PAWN,chess.KING):continue
        if before.piece_at(square)!=piece:continue
        attackers=mover_capture_map.get(square,set());defenders=new_opponent_defender_map.get(square,set())
        if not attackers or len(attackers)<=len(defenders):continue
        old_attackers=old_mover_capture_map.get(square,set())
        old_defenders=old_opponent_defender_map.get(square,set())
        if len(old_attackers)>len(old_defenders):continue
        events.append(ReviewEvent("opponent_hanging_piece",
                                  f"{move_san} leaves the opponent's {piece_label(piece)} on {chess.square_name(square)} attacked by {count_label(len(attackers),'piece')} and defended by {count_label(len(defenders),'piece')}.",
                                  "positive",focus_squares=(move.to_square,square)))

    actual_themes=tactical_themes(before,move)
    if best_move==move:
        removed_defender=removed_defender_event(before,move,best_pv,missed=False)
        if removed_defender:events.append(removed_defender)
    if after.is_checkmate():events.append(ReviewEvent("checkmate",f"{move_san} checkmates.","positive"))
    elif after.is_stalemate():events.append(ReviewEvent("stalemate",f"The game is drawn by stalemate: the side to move has no legal moves, but its king is not in check.","info"))
    elif after.is_insufficient_material():events.append(ReviewEvent("insufficient_material","The game is drawn because neither side has enough material to force checkmate.","info"))
    elif after.is_seventyfive_moves():events.append(ReviewEvent("seventyfive_move_rule","The game is drawn by the seventy-five-move rule: 75 full moves passed without a pawn move or capture.","info"))
    elif after.is_fivefold_repetition():events.append(ReviewEvent("fivefold_repetition","The game is drawn by fivefold repetition: the same position occurred at least five times.","info"))
    elif after.is_check():events.append(ReviewEvent("forcing_check",f"{move_san} gives check and forces the opponent to respond.","positive"))
    if "Fork" in actual_themes:
        targets=[f"{piece_label(target)} on {chess.square_name(square)}" for target,square in fork_targets(before,move)]
        if len(targets)>=2:
            named=" and ".join(targets[:3])
            events.append(ReviewEvent("fork",f"{move_san} forks the {', '.join(targets[:3])}.","positive",
                                      f"Your {move_san} attacks the {named}. Which target must respond first, and what can you win while it does?",
                                      tuple([move.to_square]+[square for _target,square in fork_targets(before,move)[:3]])))
    if "Promotion" in actual_themes:events.append(ReviewEvent("promotion",f"{move_san} promotes the pawn.","positive"))
    opened_targets=newly_opened_slider_targets(before,after,move.to_square)
    discovered_check=next((item for item in opened_targets if item[2].piece_type==chess.KING),None)
    if "Discovered check" in actual_themes and discovered_check:
        slider,source,target,target_square=discovered_check
        events.append(ReviewEvent("discovered_check",f"{move_san} uncovers check from your {piece_label(slider)} on {chess.square_name(source)} to the king on {chess.square_name(target_square)}.","positive",focus_squares=(source,target_square,move.to_square)))
    discovered_target=next((item for item in opened_targets if item[2].piece_type!=chess.KING),None)
    if "Discovered attack" in actual_themes and discovered_target:
        slider,source,target,target_square=discovered_target
        events.append(ReviewEvent("discovered_attack",f"{move_san} opens a line from your {piece_label(slider)} on {chess.square_name(source)} to the {piece_label(target)} on {chess.square_name(target_square)}.","positive",focus_squares=(source,target_square,move.to_square)))
    if "Skewer" in actual_themes:
        line=next(iter(newly_created_skewers(before,after,mover)),None)
        if line:
            slider,source,front,front_square,rear,rear_square=line
            events.append(ReviewEvent("skewer",f"{move_san} points your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)} behind it.","positive",
                                      f"Your {piece_label(slider)} on {chess.square_name(source)} attacks the {piece_label(front)} on {chess.square_name(front_square)} first. If it moves, what happens to the {piece_label(rear)} on {chess.square_name(rear_square)}?",
                                      (source,front_square,rear_square)))
    if before.is_castling(move):events.append(ReviewEvent("castling",f"{move_san} castles and connects the rook to the game.","positive"))

    if best_move!=move:
        best_themes=tactical_themes(before,best_move)
        labels=[name.lower() for name in ("Mate","Fork","Pin","Skewer","Discovered attack","Discovered check","Promotion","Check") if name in best_themes]
        if "Mate" in best_themes:
            events.append(ReviewEvent("missed_mate",f"The engine's best move {before.san(best_move)} forces checkmate.","critical"))
        elif explain_best_difference and "Fork" in best_themes:
            targets=[f"{piece_label(target)} on {chess.square_name(square)}" for target,square in fork_targets(before,best_move)]
            description=f" forks the {', '.join(targets[:3])}" if len(targets)>=2 else " creates a fork"
            prompt=(f"The missed {before.san(best_move)} attacks the {' and '.join(targets[:3])}. Which target must respond first, and what can you win while it does?"
                    if len(targets)>=2 else "Before choosing, which forcing move attacks more than one valuable target?")
            focus=tuple([best_move.from_square,best_move.to_square]+[square for _target,square in fork_targets(before,best_move)[:3]])
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{description}.","warning",prompt,focus))
        elif explain_best_difference and "Skewer" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            line=next(iter(newly_created_skewers(before,best_after,mover)),None)
            if line:
                slider,source,front,front_square,rear,rear_square=line
                detail=f" points your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)} behind it"
                prompt=f"The missed {before.san(best_move)} lines your {piece_label(slider)} on {chess.square_name(source)} through the {piece_label(front)} on {chess.square_name(front_square)} toward the {piece_label(rear)} on {chess.square_name(rear_square)}. What happens to the rear target when the front one moves?"
            else:
                detail=" creates a skewer";prompt="Could a line piece attack a valuable target first and another piece behind it?"
            focus=(source,front_square,rear_square) if line else (best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{detail}.","warning",prompt,focus))
        elif explain_best_difference and "Pin" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            pin=next((item for item in newly_pinned_pieces(before,best_after) if item[2].color!=mover),None)
            if pin:
                pinner,pinner_square,piece,square=pin
                detail=f" pins the opponent's {piece_label(piece)} on {chess.square_name(square)} to its king with your {piece_label(pinner)} on {chess.square_name(pinner_square)}"
                pinned_king=best_after.king(piece.color)
                prompt=f"The missed {before.san(best_move)} pins the {piece_label(piece)} on {chess.square_name(square)} to the king on {chess.square_name(pinned_king)}. What changes if that piece moves?"
                focus=(pinner_square,square,pinned_king) if pinned_king is not None else (pinner_square,square)
            else:
                pin=next(iter(newly_relative_pins(before,best_after,mover)),None)
                if pin:
                    pinner,pinner_square,piece,square,target,target_square=pin
                    detail=f" pins the opponent's {piece_label(piece)} on {chess.square_name(square)} against the {piece_label(target)} on {chess.square_name(target_square)} with your {piece_label(pinner)} on {chess.square_name(pinner_square)}"
                    prompt=f"The missed {before.san(best_move)} lines your {piece_label(pinner)} on {chess.square_name(pinner_square)} through the {piece_label(piece)} on {chess.square_name(square)} to the {piece_label(target)} on {chess.square_name(target_square)}. What is exposed if the pinned piece moves?"
                    focus=(pinner_square,square,target_square)
                else:
                    detail=" creates a pin";prompt="Can a line piece make an enemy piece unsafe to move by exposing its king or a more valuable piece?";focus=(best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)}{detail}.","warning",prompt,focus))
        elif explain_best_difference and (removed_defender:=removed_defender_event(before,best_move,best_pv)):
            events.append(removed_defender)
        elif explain_best_difference and "Winning material" in best_themes and not best_move.promotion:
            victim,capture_square=captured_piece_details(before,best_move)
            detail=f"takes the {piece_label(victim)} on {chess.square_name(capture_square)}" if victim else "wins material"
            events.append(ReviewEvent("missed_material",f"The engine's best move {before.san(best_move)} {detail}.","warning",focus_squares=(best_move.from_square,best_move.to_square,capture_square)))
        elif explain_best_difference and best_move.promotion:
            promoted=piece_label(chess.Piece(best_move.promotion,mover));best_after=before.copy(stack=False);best_after.push(best_move)
            king=best_after.king(opponent);check_text=f" with check on the king on {chess.square_name(king)}" if best_after.is_check() and king is not None else ""
            prompt=f"The missed {before.san(best_move)} promotes the pawn on {chess.square_name(best_move.from_square)} to a {promoted} on {chess.square_name(best_move.to_square)}. What forcing threats does the new piece create?"
            focus=(best_move.from_square,best_move.to_square)+((king,) if check_text and king is not None else ())
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} promotes the pawn on {chess.square_name(best_move.from_square)} to a {promoted} on {chess.square_name(best_move.to_square)}{check_text}.","warning",prompt,focus))
        elif explain_best_difference and "Discovered attack" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            opened=newly_opened_slider_targets(before,best_after,best_move.to_square)
            target=next((item for item in opened if item[2].piece_type in (chess.QUEEN,chess.ROOK)),None)
            if target:
                slider,source,piece,target_square=target;moved=before.piece_at(best_move.from_square)
                detail=f"moves your {piece_label(moved)} from {chess.square_name(best_move.from_square)}, uncovering your {piece_label(slider)} on {chess.square_name(source)} against the {piece_label(piece)} on {chess.square_name(target_square)}"
                prompt=f"The missed {before.san(best_move)} uncovers your {piece_label(slider)} on {chess.square_name(source)} against the {piece_label(piece)} on {chess.square_name(target_square)}. How can you exploit that line?"
                focus=(best_move.from_square,best_move.to_square,source,target_square)
            else:
                detail="uncovers an attack from a line piece";prompt="Which piece moves out of the way, and what valuable target does the line piece attack?";focus=(best_move.from_square,best_move.to_square)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} {detail}.","warning",prompt,focus))
        elif explain_best_difference and "Check" in best_themes:
            best_after=before.copy(stack=False);best_after.push(best_move)
            king=best_after.king(opponent);checkers=list(best_after.checkers())
            checker_square=checkers[0] if checkers else best_move.to_square
            checker=best_after.piece_at(checker_square)
            king_name=chess.square_name(king) if king is not None else "the king"
            if checker_square!=best_move.to_square:
                moved=before.piece_at(best_move.from_square)
                detail=f"uncovers check from your {piece_label(checker)} on {chess.square_name(checker_square)} to the king on {king_name} by moving the {piece_label(moved)}"
            else:
                detail=f"gives check from your {piece_label(checker)} on {chess.square_name(checker_square)} to the king on {king_name}"
            prompt=f"The missed {before.san(best_move)} checks the king on {king_name}. Which legal replies meet the threat without losing material?"
            focus=tuple([best_move.from_square,best_move.to_square,checker_square]+([king] if king is not None else []))
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} {detail}.","warning",prompt,focus))
        elif explain_best_difference and labels:
            description=" and ".join(labels)
            events.append(ReviewEvent("missed_tactic",f"The best move {before.san(best_move)} creates a {description}.","warning"))
        if (explain_best_difference
                and not any(event.kind in ("missed_mate","missed_tactic","missed_material") for event in events)):
            plan=missed_plan_event(before,move,after,best_move)
            if plan:events.append(plan)

    # Name only pins newly created by the move; include absolute pins to the
    # king and relative pins to a more valuable rook or queen.
    absolute_pins=[(pinner,pinner_square,piece,square,None,None)
                   for pinner,pinner_square,piece,square in newly_pinned_pieces(before,after)]
    relative_pins=[]
    for attacker in (mover,opponent):relative_pins.extend(newly_relative_pins(before,after,attacker))
    pins=absolute_pins+relative_pins
    if pins:
        pinner,pinner_square,piece,square,target,target_square=next(
            (pin for pin in pins if pin[2].color==mover),pins[0])
        who="your" if piece.color==mover else "the opponent's"
        pinner_name=piece_label(pinner);pin_square=chess.square_name(pinner_square)
        piece_name=piece_label(piece);piece_square=chess.square_name(square)
        if target is None:
            pinned_king=after.king(piece.color);target_name="king";target_square_name=chess.square_name(pinned_king) if pinned_king is not None else "its king"
            detail=f"pins {who} {piece_name} on {piece_square} to the {target_name} on {target_square_name}"
            prompt=(f"Your {pinner_name} on {pin_square} pins the {piece_name} on {piece_square} to the king on {target_square_name}. What would happen if the pinned piece moved?"
                    if piece.color!=mover else f"The opponent's {pinner_name} on {pin_square} pins your {piece_name} on {piece_square}. What valuable piece or square is behind it?")
        else:
            target_name=piece_label(target);target_square_name=chess.square_name(target_square)
            detail=f"pins {who} {piece_name} on {piece_square} against the {target_name} on {target_square_name}"
            prompt=(f"Your {pinner_name} on {pin_square} lines up the {piece_name} on {piece_square} with the {target_name} on {target_square_name}. What is exposed if the pinned piece moves?"
                    if piece.color!=mover else f"The opponent's {pinner_name} on {pin_square} lines up your {piece_name} on {piece_square} with the {target_name} on {target_square_name}. What could be lost if it moves?")
        focus=(pinner_square,square)+( (target_square,) if target is not None else ((after.king(piece.color),) if after.king(piece.color) is not None else ()) )
        events.append(ReviewEvent("new_pin",f"The {pinner_name} on {pin_square} {detail}.","warning" if piece.color==mover else "positive",prompt,focus))

    # Explain structural consequences only when this move measurably changes them.
    before_doubled,before_isolated=_pawn_structure(before,mover);after_doubled,after_isolated=_pawn_structure(after,mover)
    new_doubled=after_doubled-before_doubled;new_isolated=after_isolated-before_isolated
    if new_doubled:
        files=", ".join(chr(ord("a")+file)+"-file" for file in sorted(new_doubled))
        events.append(ReviewEvent("pawn_structure",f"The move leaves your pawns doubled on the {files}.","warning"))
    elif new_isolated:
        files=", ".join(chr(ord("a")+file)+"-file" for file in sorted(new_isolated))
        events.append(ReviewEvent("pawn_structure",f"The move leaves a pawn isolated on the {files}.","warning"))

    pressure_before=_king_pressure(before,mover);pressure_after=_king_pressure(after,mover)
    if pressure_after-pressure_before>=2:
        king=after.king(mover)
        events.append(ReviewEvent("king_exposure",f"The move increases attacks around your king on {chess.square_name(king)} from {pressure_before} to {pressure_after} nearby squares.","warning"))
    elif pressure_before-pressure_after>=2:
        king=after.king(mover)
        events.append(ReviewEvent("king_safety",f"The move reduces enemy attacks around your king on {chess.square_name(king)} from {pressure_before} to {pressure_after} nearby squares.","positive"))
    center_before=_center_control(before,mover);center_after=_center_control(after,mover)
    if center_before-center_after>=2:
        events.append(ReviewEvent("center_control",f"The move reduces your attacks on the four central squares from {center_before} to {center_after}.","warning"))
    elif center_after-center_before>=2:
        events.append(ReviewEvent("central_control",f"The move increases your attacks on the four central squares from {center_before} to {center_after}.","positive"))

    if mover_piece and mover_piece.piece_type in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN):
        mobility_before=_piece_mobility(before,mover,move.from_square)
        mobility_after=_piece_mobility(after,mover,move.to_square)
        if mobility_after-mobility_before>=2:
            events.append(ReviewEvent("piece_activity",f"Your {piece_label(mover_piece)} improves its legal mobility from {mobility_before} to {mobility_after} moves.","positive"))
        elif mobility_before-mobility_after>=3:
            events.append(ReviewEvent("restricted_piece",f"Your {piece_label(mover_piece)} becomes more restricted: its legal mobility falls from {mobility_before} to {mobility_after} moves.","warning"))

    home_rank=0 if mover==chess.WHITE else 7
    if mover_piece and mover_piece.piece_type in (chess.KNIGHT,chess.BISHOP) and chess.square_rank(move.from_square)==home_rank and chess.square_rank(move.to_square)!=home_rank:
        events.append(ReviewEvent("development",f"{move_san} develops your {piece_label(mover_piece)} from its starting square.","positive"))

    if (repeated_piece_moves>=3 and mover_piece and before.legal_moves.count()>1
            and not before.is_check() and not before.is_capture(move) and not after.is_check()):
        suffix="th" if 10<=repeated_piece_moves%100<=20 else {1:"st",2:"nd",3:"rd"}.get(repeated_piece_moves%10,"th")
        events.append(ReviewEvent("repeat_piece",f"This is the {repeated_piece_moves}{suffix} move by the same {piece_label(mover_piece)}; check that the reroute has gained something, since repeated moves can cost development time.","context"))

    # The strongest response line can establish a trapped piece: it is under
    # attack and has no legal safe retreat. Do not infer this from geometry alone.
    if response_takes and response_victim and response_victim.piece_type not in (chess.PAWN,chess.KING):
        target_square=response_capture_square
        if len(set(after.attackers(opponent,target_square)))>len(set(after.attackers(mover,target_square))):
            safe_exits=_safe_piece_escapes(after,mover,target_square)
            if safe_exits==0:
                events.append(ReviewEvent("trapped_piece",f"Your {piece_label(response_victim)} on {response_square} has no safe legal escape before the opponent's capture.","critical"))
    return tuple(events)


def review_reflection_prompt(events: tuple[ReviewEvent,...]) -> Optional[str]:
    """Choose one short question grounded in a verified event from this move."""
    prompts={
        "lost_defender":"Before moving a defender, which other piece still depends on it?",
        "hanging_piece":"Before committing, what can the opponent capture immediately?",
        "trapped_piece":"If this piece is attacked next, where can it retreat safely?",
        "missed_mate":"Start with forcing checks: which king replies can you rule out?",
        "missed_tactic":"Before a quiet move, did you check captures, checks, and direct threats?",
        "missed_plan":"What concrete positional improvement does the stronger move create?",
        "missed_material":"After the forcing line, which loose piece can still be won?",
        "opponent_mate":"Which flight square or defender could stop the mating net?",
        "back_rank_mate":"Could a flight square or back-rank defender prevent mate?",
        "opponent_capture":"After the strongest reply, which of your pieces is under attack?",
        "pv_material_loss":"Follow the exchange sequence: where does the material balance turn?",
        "pv_material_gain":"Trace the engine's line: which exchange leaves you ahead in material?",
        "fork":"Which two valuable targets are hit, and which one must respond first?",
        "discovered_check":"Which line piece gives check when the blocker moves?",
        "discovered_attack":"Which line opened, and what target is now attacked?",
        "skewer":"Which front piece must move, exposing the target behind it?",
        "sound_sacrifice":"If the opponent accepts the sacrifice, where does your compensation come from?",
        "removed_defender":"Which capture removes the defender, and what valuable target becomes vulnerable?",
        "only_good_move":"What makes the alternatives fail to hold equality in this position?",
        "turnaround":"Which feature of this position lets this move turn a likely loss into a drawable game?",
        "new_pin":"What valuable piece or square lies behind the pinned piece?",
        "king_exposure":"Which file or diagonal opened toward your king after this move?",
        "king_safety":"Which escape square or line is safer for your king now?",
        "pawn_structure":"Will this pawn move leave a weakness that cannot move again?",
        "center_control":"Which central squares did this move stop contesting?",
        "central_control":"Which central squares does this move help you control?",
        "repeat_piece":"What concrete threat or improvement pays for moving this piece again?",
        "restricted_piece":"What route could give this piece a useful square?",
        "piece_activity":"Where can this more active piece go next?",
        "development":"What new squares or plans does this developed piece enable?",
        "castling":"How does castling help connect your rook to the plan?",
        "promotion":"What new threats does the promoted piece create?",
        "checkmate":"Which escape squares make this checkmate work?",
    }
    priorities={"lost_defender":0,"hanging_piece":0,"trapped_piece":0,"missed_mate":0,
                "missed_tactic":1,"missed_plan":2,"opponent_mate":1,"back_rank_mate":1,"opponent_capture":2,
                "pv_material_loss":2,"pv_material_gain":1,"fork":2,"discovered_check":2,"discovered_attack":2,
                "skewer":2,"sound_sacrifice":2,"new_pin":3,"king_exposure":3,"pawn_structure":3,
                "center_control":3,"central_control":3,"repeat_piece":3,"king_safety":4,
                "restricted_piece":4,"piece_activity":4,"development":4,"castling":4,
                "promotion":2,"checkmate":1,"missed_material":2,"removed_defender":2,
                "only_good_move":1}
    candidates=sorted((event for event in events if event.kind in prompts),
                      key=lambda event:priorities.get(event.kind,5))
    return (candidates[0].prompt or prompts[candidates[0].kind]) if candidates else None


def review_focus_squares(events: tuple[ReviewEvent,...]) -> tuple[chess.Square,...]:
    """Choose the most useful verified target squares to mark on the review board."""
    priority={"lost_defender":0,"hanging_piece":0,"trapped_piece":0,"missed_mate":0,
              "opponent_mate":0,"back_rank_mate":0,"opponent_capture":1,"pv_material_loss":1,"pv_material_gain":1,
              "missed_tactic":1,"missed_material":1,"fork":1,"discovered_check":1,
              "discovered_attack":1,"skewer":1,"new_pin":1,"removed_defender":1,
              "only_good_move":1,"turnaround":1,"missed_plan":2,"capture":3}
    ordered=sorted((event for event in events if event.focus_squares),
                   key=lambda event:priority.get(event.kind,4))
    if not ordered:return ()
    return tuple(dict.fromkeys(square for square in ordered[0].focus_squares if square is not None))[:4]


def review_practice_hint(board: chess.Board, best_move: chess.Move,
                        best_pv: tuple[chess.Move,...] = (), level: int = 1) -> str:
    """Give progressively more specific, non-spoiling hints from the engine's move."""
    if best_move not in board.legal_moves:
        return "Compare forcing checks and captures first; then improve your least active piece."
    themes=tactical_themes(board,best_move)
    after=board.copy(stack=False);after.push(best_move)
    events=None
    if level>=2:
        if "Fork" in themes:
            targets=fork_targets(board,best_move)
            names=[f"the {piece_label(piece)} on {chess.square_name(square)}" for piece,square in targets[:3]]
            if len(names)>=2:return f"The targets are {' and '.join(names)}. Look for one move that attacks both at once."
        if "Discovered check" in themes or "Discovered attack" in themes:
            opened=newly_opened_slider_targets(board,after,best_move.to_square)
            target=next((entry for entry in opened if entry[2].piece_type==chess.KING),None) if "Discovered check" in themes else None
            target=target or next((entry for entry in opened if entry[2].piece_type in (chess.QUEEN,chess.ROOK)),None)
            if target:
                slider,source,piece,square=target
                target_name="the king" if piece.piece_type==chess.KING else f"the {piece_label(piece)}"
                return (f"Watch the line from your {piece_label(slider)} on {chess.square_name(source)} "
                        f"toward {target_name} on {chess.square_name(square)}. Which blocker can move to open it?")
        if "Skewer" in themes:
            line=next(iter(newly_created_skewers(board,after,board.turn)),None)
            if line:
                slider,source,front,front_square,rear,rear_square=line
                return (f"Look at the line from your {piece_label(slider)} on {chess.square_name(source)} "
                        f"through the {piece_label(front)} on {chess.square_name(front_square)} toward "
                        f"the {piece_label(rear)} on {chess.square_name(rear_square)} behind it.")
        if "Pin" in themes:
            pin=next((entry for entry in newly_pinned_pieces(board,after) if entry[2].color!=board.turn),None)
            if pin:
                pinner,pinner_square,piece,square=pin
                return (f"A line from your {piece_label(pinner)} on {chess.square_name(pinner_square)} "
                        f"can pin the {piece_label(piece)} on {chess.square_name(square)}. What valuable target lies behind it?")
            relative_pin=next(iter(newly_relative_pins(board,after,board.turn)),None)
            if relative_pin:
                pinner,pinner_square,piece,square,target,target_square=relative_pin
                return (f"Look for a line from your {piece_label(pinner)} on {chess.square_name(pinner_square)} "
                        f"through the {piece_label(piece)} on {chess.square_name(square)} toward "
                        f"the {piece_label(target)} on {chess.square_name(target_square)}.")
        if "Promotion" in themes:
            return f"The pawn on {chess.square_name(best_move.from_square)} is ready to promote. Check whether the promotion also creates check or a direct threat."
        if "Winning material" in themes:
            victim,square=captured_piece_details(board,best_move)
            if victim and square is not None:
                return f"The target is the {piece_label(victim)} on {chess.square_name(square)}. Count its defenders and the recapture before taking it."
        if "Mate" in themes:
            return "Start with checks that restrict the king's escape squares; compare the opponent's only legal replies."
        if "Check" in themes:
            checkers=list(after.checkers())
            checker=after.piece_at(checkers[0]) if checkers else None
            square=chess.square_name(checkers[0]) if checkers else chess.square_name(best_move.to_square)
            piece_text=f"your {piece_label(checker)}" if checker else "your moved piece"
            return f"The checking piece is {piece_text} on {square}. Calculate each legal king reply before choosing your move."
        positional_details={
            "central_control":"Compare which candidate move increases your attacks on d4, e4, d5, and e5.",
            "development":"Look at the pieces still on their starting squares; which one can join the center with tempo?",
            "castling":"Check whether castling is legal and how it would secure your king while activating the rook.",
            "king_safety":"Look for the open files and diagonals pointing toward your king, then find a move that blocks or reduces that pressure.",
            "piece_activity":"Find your least active piece and compare the useful squares it can reach after the move.",
            "new_pin":"Look for a line piece that can tie an enemy piece to its king or a more valuable piece.",
        }
        events=analyze_review_events(board,best_move,after,best_move,best_pv=best_pv[:10])
        for event in events:
            if event.kind in positional_details:return positional_details[event.kind]
    tactical_hints=(
        ("Mate","Look for a forcing check that leaves the king with no escape."),
        ("Fork","Look for a move that attacks two valuable targets at once."),
        ("Discovered check","Can moving a blocker uncover check from a line piece?"),
        ("Discovered attack","Can moving a blocker uncover an attack on a valuable piece?"),
        ("Skewer","Can you line up a valuable front piece with another target behind it?"),
        ("Pin","Look for a line piece that makes an enemy piece unsafe to move."),
        ("Promotion","Can a pawn advance with promotion, or create an unavoidable promotion threat?"),
        ("Winning material","Look for a forcing capture or sequence that wins material."),
        ("Check","Try forcing checks first and see whether the reply leaves a second threat."),
    )
    for theme,hint in tactical_hints:
        if theme in themes:return hint
    if events is None:
        response=best_pv[1] if len(best_pv)>1 and best_pv[1] in after.legal_moves else None
        response_pv=best_pv[2:10] if response else ()
        events=analyze_review_events(board,best_move,after,best_move,response,best_pv[:10],response_pv=response_pv)
    positional_hints={
        "central_control":"Look for a move that expands your control of the central squares.",
        "development":"Look for a developing move that gives a piece more useful squares.",
        "castling":"Look for a way to secure your king and connect your rook to the game.",
        "king_safety":"Look for a move that reduces the opponent's pressure around your king.",
        "piece_activity":"Improve the mobility of your least active piece.",
        "new_pin":"Look for a line that ties an enemy piece to a more valuable target behind it.",
    }
    for event in events:
        if event.kind in positional_hints:return positional_hints[event.kind]
    return "Compare forcing checks and captures first; if none work, improve your least active piece."


def generate_review_commentary(verdict: str, san: str, best_san: str, before_cp: int, after_cp: int,
                               loss: int, events: tuple[ReviewEvent,...], response: str = "",
                               best_reason: str = "", best_pv: str = "",
                               expected_loss: Optional[float] = None,
                               variation_seed: int = 0) -> str:
    """Turn verified events into concise coaching text; use eval-only language as fallback."""
    priority={"lost_defender":0,"hanging_piece":0,"trapped_piece":1,"missed_mate":0,"missed_tactic":1,"missed_plan":2,
              "opponent_capture":2,"pv_material_loss":1,"pv_material_gain":1,"opponent_mate":0,"back_rank_mate":0,"opponent_check":4,"new_pin":2,
              "fork":1,"discovered_attack":1,"forcing_check":2,"checkmate":0,"promotion":2,
              "discovered_check":1,"skewer":1,"removed_defender":1,"only_good_move":1,"turnaround":1,
              "pawn_structure":3,"king_exposure":3,"king_safety":2,"center_control":4,"central_control":3,"restricted_piece":3,
              "piece_activity":3,"development":3,"castling":3,"sound_sacrifice":0,"repeat_piece":5}
    ordered=sorted(events,key=lambda event:priority.get(event.kind,6))
    voice_seed=(sum(ord(char) for char in san+best_san)
                +sum(ord(char) for event in ordered for char in event.kind)
                +max(0,int(variation_seed)))
    saturated_gap=(f"The expected-outcome estimate is nearly flat, but Stockfish's evaluation shifts "
                   f"{loss/100:.1f} pawns against the side that moved; {best_san} keeps more of the position.")
    show_saturated_gap=(expected_loss is not None and expected_loss<=.01 and loss>=250
                        and best_san and best_san!=san)
    mobility_alternative=any(event.kind=="missed_plan" and "legal moves" in event.text for event in ordered)
    concrete=[event.text for event in ordered if event.severity in ("critical","warning") and event.kind!="opponent_check"
              and not (mobility_alternative and event.kind=="restricted_piece")]
    if verdict=="Brilliant":
        sacrifice=next((event.text for event in ordered if event.kind=="sound_sacrifice"),None)
        if sacrifice:return f"Brilliant: {sacrifice}"
        return (f"Unverified move: Stockfish did not confirm a concrete sacrifice for {san}; "
                "do not label it Brilliant.")
    if verdict in ("Blunder","Mistake","Inaccuracy","Miss"):
        if concrete:
            cause=" ".join(concrete[:2])
            openings=(f"{verdict}: {cause}",
                      f"{verdict}: The line after {san} shows the key problem: {cause}",
                      f"{verdict}: Watch the opponent's reply. {cause}",
                      f"{verdict}: This is where the position turns. {cause}",
                      f"{verdict}: The move's concrete problem is clear in the line. {cause}")
            explanation=openings[voice_seed%len(openings)]
            if best_san and best_san!=san and not any(event.kind in ("missed_mate","missed_tactic","missed_plan") for event in ordered):
                alternatives=(f"The stronger alternative was {best_san}.",
                              f"{best_san} avoids this problem.",
                              f"Stockfish prefers {best_san} here.")
                explanation+=f" {alternatives[(voice_seed//len(openings))%len(alternatives)]}"
            return explanation
        return (f"{verdict}: Stockfish prefers {best_san}; the evaluation for the mover shifts from "
                f"{eval_text(before_cp)} to {eval_text(after_cp)} ({loss/100:.1f} pawns by the engine's estimate). "
                "This search did not confirm a specific tactical cause, so treat this as an evaluation-based warning.")
    if verdict=="Book":return f"Book move: {san} follows the loaded local opening book."
    if verdict=="Forced":return f"Forced: {san} was the only legal move."
    positive=[event.text for event in ordered if event.severity=="positive"]
    contextual=[event.text for event in ordered if event.severity=="context"]
    if positive or contextual:
        details=positive[:2] or contextual[:1]
        detail_text=" ".join(details)
        lead_event=next((event for event in ordered if event.severity=="positive"),None)
        # Keep reviews reproducible while avoiding the same stock sentence on
        # every move. The wording is selected from the SAN and verified themes.
        idea_seed=(sum(ord(char) for char in (lead_event.kind if lead_event else ""))
                   +max(0,int(variation_seed)))
        idea_leads={
            "fork":"The double attack is the key idea.",
            "pv_material_gain":"The engine's line confirms a concrete material gain.",
            "skewer":"The more valuable front piece is under attack, with another target behind it.",
            "discovered_attack":"Moving the blocker exposes a target to the line piece.",
            "discovered_check":"The discovered check forces the king to respond.",
            "removed_defender":"Removing that defender is the tactical point.",
            "new_pin":"The pin ties the opponent's piece to a more valuable target.",
            "sound_sacrifice":"The sacrifice is backed by the engine's continuation.",
            "promotion":"The pawn's promotion changes the position immediately.",
            "checkmate":"The move ends the game with a forced mate.",
            "forcing_check":"The check keeps the opponent on the defensive.",
            "king_safety":"This gives your king a safer position.",
            "central_control":"This strengthens your control of the center.",
            "development":"This gets a new piece into the game.",
            "piece_activity":"The improvement in piece activity is the main gain.",
            "castling":"This secures the king and brings the rook closer to play.",
            "pawn_structure":"The pawn structure is the lasting feature of this move.",
        }
        idea_lead=idea_leads.get(lead_event.kind,"This creates a concrete idea.") if lead_event else ""
        if positive and verdict=="Best":
            if best_san==san:
                lead=("Stockfish's top choice is the move you played.",
                      "You found the engine's preferred move.",
                      "The engine's first choice is yours.",
                      "Your move matches its leading line.",
                      "This is the engine's top-scoring option.")[idea_seed%5]
            else:
                lead=("This is one of Stockfish's strongest choices.",
                      "Stockfish also rates this among its leading options.",
                      "The engine considers this a top-tier alternative.",
                      "Your choice stays near the top of the engine's list.",
                      "The move is close to the engine's first choice.")[idea_seed%5]
            return f"Best: {lead} {detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        if positive and verdict=="Excellent":
            closeness=("It stays close to Stockfish's top choice. ",
                       "You keep nearly all the value of the best line. ",
                       "The engine's preferred move is only a small step ahead. ",
                       "The evaluation remains close to its leading line. ",
                       "Only a little expected score separates this from the best move. ")[idea_seed%5]
            return f"Excellent: {idea_lead} {closeness}{detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        if positive and verdict=="Good":
            value_note=("It keeps most of the position's value. ",
                        "You preserve most of the expected score available here. ",
                        "Most of the position's expected score remains. ",
                        "The move gives up only a small part of the engine's estimate. ",
                        "You keep most of the value, though Stockfish found a little more. ")[idea_seed%5]
            return f"Good: {idea_lead} {value_note}{detail_text}"+(f" {saturated_gap}" if show_saturated_gap else "")
        return f"{verdict}: "+detail_text+(f" {saturated_gap}" if show_saturated_gap else "")
    if best_reason and best_san!=san:
        return f"{verdict}: {san} is playable, while {best_san} is the engine's top choice. {best_reason}"+(f" {saturated_gap}" if show_saturated_gap else "")
    line=f" The engine's continuation is {best_pv}." if best_pv else ""
    response_line=f" The opponent's strongest reply is {response}." if response else ""
    return f"{verdict}: {san} keeps the position close to the engine's preferred evaluation.{response_line}{line}"+(f" {saturated_gap}" if show_saturated_gap else "")


def game_coach_summary(items: list[ReviewMove], user_color: chess.Color) -> str:
    """Summarize patterns seen in this game only; do not infer player traits."""
    own=[item for item in items if (item.side=="White") == (user_color==chess.WHITE)]
    if not own:return "No moves from your side were available for a game-specific summary."
    weakness_map={"lost_defender":"pieces left without their defenders","hanging_piece":"undefended pieces",
                  "trapped_piece":"pieces with no safe retreat","missed_tactic":"missed tactical chances",
                  "missed_plan":"positional improvements left unused",
                  "missed_mate":"missed forced mates","back_rank_mate":"back-rank king safety","missed_material":"missed chances to win material",
                  "pv_material_loss":"material lost in the engine line","new_pin":"pieces left pinned","king_exposure":"king exposure","pawn_structure":"pawn-structure weaknesses",
                  "repeat_piece":"repeated moves with the same piece","restricted_piece":"restricted piece activity",
                  "opponent_capture":"allowing tactical captures","pv_material_loss":"material lost in the engine line",
                  "opponent_mate":"allowing checkmate","back_rank_mate":"back-rank king safety"}
    weakness_counts={};error_verdicts=("Miss","Inaccuracy","Mistake","Blunder")
    for item in own:
        move_weaknesses=set()
        for event in item.events:
            if (event.kind in weakness_map and item.verdict in error_verdicts
                    and event.severity in ("critical","warning","context")):
                move_weaknesses.add(event.kind)
        for kind in move_weaknesses:
            weakness_counts[kind]=weakness_counts.get(kind,0)+1
    weaknesses=sorted(((count,kind,weakness_map[kind]) for kind,count in weakness_counts.items()),reverse=True)
    recurring=[entry for entry in weaknesses if entry[0]>=2]
    voice_seed=sum(ord(char) for item in own for char in item.san)+sum(item.ply for item in own)
    def example_for(kind):
        candidates=[item for item in own if item.verdict in error_verdicts
                    and any(event.kind==kind and event.severity in ("critical","warning","context") for event in item.events)]
        if not candidates:return ""
        example=max(candidates,key=lambda item:(getattr(item,"expected_loss",0.0),-item.ply))
        event=next(event for event in example.events if event.kind==kind and event.severity in ("critical","warning","context"))
        return f" The clearest example was {example.side} {example.san} (move {(example.ply+1)//2}): {event.text}"
    coaching_tip={
        "lost_defender":"Before committing to an attack, check whether each attacked piece still has enough defenders.",
        "hanging_piece":"Before moving, scan for opponent captures that would take an undefended piece.",
        "trapped_piece":"Before advancing a piece, identify at least one safe retreat square.",
        "missed_tactic":"When the opponent moves, check captures, checks, and direct threats before choosing a plan.",
        "missed_plan":"Compare the engine's plan with yours: did it improve the center, king safety, development, or activity?",
        "missed_mate":"Look for forcing checks first, then calculate whether the king has any legal escape.",
        "back_rank_mate":"Leave a flight square or a defender near your king when the back rank is exposed.",
        "missed_material":"After every forcing move, look for loose pieces and tactics that win material.",
        "king_exposure":"Before pushing pawns near your king, check which files and diagonals will open.",
        "pawn_structure":"Check whether a pawn move creates a lasting weakness before making it.",
        "repeat_piece":"After moving the same piece again, ask what concrete threat or improvement the reroute achieves.",
        "restricted_piece":"Look for a route that gives your least active piece a useful square.",
        "opponent_capture":"Before finalizing a move, test the opponent's forcing captures and checks.",
    }
    if recurring:
        _,kind,_=recurring[0]
        example_text=example_for(kind)
        lead=("Your clearest recurring lesson was ","A pattern to work on from this game: ",
              "The recurring theme in this game was ")[voice_seed%3]
        weakness_text=lead+", ".join(label for _,_,label in recurring[:2])+"."+example_text+" "+coaching_tip.get(kind,"Review the critical moments and calculate the opponent's strongest reply.")
    elif weaknesses:
        _,kind,label=weaknesses[0]
        example_text=example_for(kind)
        lead=("One useful lesson from this game is to watch for ",
              "This game offered a chance to improve around ",
              "Keep an eye on ")[voice_seed%3]
        weakness_text=lead+label+"."+example_text+" "+coaching_tip.get(kind,"Review this moment and compare your move with the engine's strongest reply.")
    else:
        major=[item for item in own if item.verdict in ("Miss","Blunder","Mistake","Inaccuracy")]
        if major:
            worst=max(major,key=lambda item:item.expected_loss)
            weakness_text=f"The largest unexplained evaluation swing was {worst.san} ({worst.verdict}); revisit the engine line for that move."
        else:weakness_text="The review found no repeated tactical or positional weakness in your moves."
    strengths=[]
    tactical_moves=[item for item in own if has_review_tactical_idea(item)]
    quality_moves=sum(item.verdict in ("Best","Excellent","Good","Great","Brilliant") for item in own)
    accurate=sum(item.verdict in ("Best","Excellent","Great","Brilliant") for item in own)
    if tactical_moves:
        example=max(tactical_moves,key=lambda item:(getattr(item,"expected_loss",0.0),-item.ply))
        event=next(event for event in example.events
                   if event.kind in TACTICAL_REVIEW_EVENT_KINDS and event.severity=="positive")
        tactical_count="a tactical idea in one move" if len(tactical_moves)==1 else f"tactical ideas in {len(tactical_moves)} moves"
        tactic_phrasing=(f"You found {tactical_count}, including {review_move_notation(example)}: {event.text}",
                         f"A tactical highlight was {review_move_notation(example)}: {event.text}",
                         f"You spotted a concrete tactic on {review_move_notation(example)}: {event.text}")
        strengths.append(tactic_phrasing[voice_seed%len(tactic_phrasing)])
    if quality_moves:
        quality_count="one of your moves" if quality_moves==1 else f"{quality_moves} of your moves"
        accuracy_count="one reached" if accurate==1 else f"{accurate} reached"
        accuracy_note=(f"; {accuracy_count} Excellent or above" if accurate
                       else "; none reached Excellent or above")
        accuracy_phrasing=(f"Stockfish rated {quality_count} Good or better{accuracy_note}.",
                           f"You earned Good-or-better ratings on {quality_count}{accuracy_note}.",
                           f"{quality_count.capitalize()} earned a Good-or-better label{accuracy_note}.")
        strengths.append(accuracy_phrasing[(voice_seed+1)%len(accuracy_phrasing)])
    if strengths:
        intro=("A strength in this game: ","One thing you did well: ","A bright spot from this game: ")[voice_seed%3]
        return weakness_text+" "+intro+" ".join(strengths)
    return weakness_text


def recurring_review_themes(game_stats: list[dict], limit: int = 3) -> list[tuple[str,int]]:
    """Count error themes by distinct reviewed games, not event frequency."""
    games_by_theme={}
    for stats in game_stats:
        themes=stats.get("error_themes",{}) if isinstance(stats,dict) else {}
        if not isinstance(themes,dict):continue
        for name,count in themes.items():
            try:present=int(count)>0
            except (TypeError,ValueError):present=False
            if present:games_by_theme[name]=games_by_theme.get(name,0)+1
    recurring=[(name,count) for name,count in games_by_theme.items() if count>=2]
    return sorted(recurring,key=lambda item:(-item[1],item[0]))[:max(0,limit)]


def recurring_review_strengths(game_stats: list[dict], limit: int = 2) -> list[tuple[str,int]]:
    """Count engine-approved positive themes by distinct reviewed games."""
    games_by_strength={}
    for stats in game_stats:
        strengths=stats.get("strength_themes",{}) if isinstance(stats,dict) else {}
        if not isinstance(strengths,dict):continue
        for name,count in strengths.items():
            try:present=int(count)>0
            except (TypeError,ValueError):present=False
            if present:games_by_strength[name]=games_by_strength.get(name,0)+1
    recurring=[(name,count) for name,count in games_by_strength.items() if count>=2]
    return sorted(recurring,key=lambda item:(-item[1],item[0]))[:max(0,limit)]


def personalized_review_coach(items: list[ReviewMove], user_color: chess.Color,
                             saved_game_stats: list[dict]) -> str:
    """Connect this game's lesson to themes verified across separate saved games."""
    summary=game_coach_summary(items,user_color)
    recurring=recurring_review_themes(saved_game_stats,limit=1)
    if not recurring:return summary
    theme,games=recurring[0]
    return (f"{summary} Across your saved reviews, the recurring pattern is “{theme}”; "
            f"it appeared in {games} separate games. Watch for it in your next game.")


def player_review_overview(items: list[ReviewMove], user_color: chess.Color) -> str:
    """Summarize player-only move quality by phase and their biggest swing."""
    own=[item for item in items if (item.side=="White") == (user_color==chess.WHITE)]
    if not own:return "No moves from your side were available in this review."
    accuracy=review_accuracy(own)
    biggest=max(own,key=lambda item:item.expected_loss)
    phase_scores=[]
    for phase in ("Opening","Middlegame","Endgame"):
        phase_moves=[item for item in own if getattr(item,"phase","")==phase]
        if phase_moves:
            score=review_accuracy(phase_moves)
            phase_scores.append(f"{phase} {score:.0f}% ({len(phase_moves)} moves)")
    phase_text=" · By phase: "+", ".join(phase_scores) if phase_scores else ""
    return (f"Your move quality: {accuracy:.0f}%{phase_text} · Your biggest swing was your {biggest.san} "
            f"({biggest.verdict}, {biggest.expected_loss*100:.1f} pp expected score lost).")


def review_filter_indices(items: list[ReviewMove], total_plies: int, filter_name: str,
                          player_color: chess.Color, start_turn: chess.Color,
                          side_filter: str = "Both sides") -> list[int]:
    """Return visible zero-based ply indexes for the review move-list filter."""
    visible=[]
    phase_filters={"Opening","Middlegame","Endgame"}
    analysis_filters={"Errors","Missed chances","Good moves","Brilliant / Great",
                      "Book moves","Tactical ideas"}|phase_filters
    for index in range(total_plies):
        item=items[index] if index<len(items) else None
        mover=(start_turn if index%2==0 else not start_turn)
        if (filter_name=="My moves" or side_filter=="My moves") and mover!=player_color:continue
        if side_filter=="White" and mover!=chess.WHITE:continue
        if side_filter=="Black" and mover!=chess.BLACK:continue
        if filter_name in analysis_filters and item is None:continue
        if item and filter_name in phase_filters and item.phase!=filter_name:continue
        if item and filter_name=="Errors" and item.verdict not in ("Inaccuracy","Mistake","Blunder"):continue
        if item and filter_name=="Missed chances" and item.verdict!="Miss":continue
        if item and filter_name=="Good moves" and item.verdict not in ("Brilliant","Great","Best","Excellent","Good","Book"):continue
        if item and filter_name=="Brilliant / Great" and item.verdict not in ("Brilliant","Great"):continue
        if item and filter_name=="Book moves" and not item.book_status.startswith("Book move"):continue
        if filter_name=="Tactical ideas" and not (item and has_review_tactical_idea(item)):continue
        visible.append(index)
    return visible


def review_row_ply(visible_plies: list[int], row: int) -> Optional[int]:
    """Map a selected review row to its game ply, ignoring cleared selections."""
    return visible_plies[row] if 0<=row<len(visible_plies) else None


def review_practice_moves(items: list[ReviewMove], player_color: chess.Color) -> list[ReviewMove]:
    """Select only the player's own reviewed errors for the default retry session."""
    player_side="White" if player_color==chess.WHITE else "Black"
    errors={"Miss","Inaccuracy","Mistake","Blunder"}
    return [item for item in items if item.side==player_side and item.verdict in errors]


def rank_review_moments(items: list[ReviewMove], limit: int = 8) -> list[ReviewMove]:
    """Select the most consequential review moments, then return them in game order."""
    labels={"Brilliant":.18,"Great":.10,"Miss":.04,"Blunder":0.0,"Mistake":0.0,"Inaccuracy":0.0}
    limit=max(0,limit)
    if not limit:return []
    terminal_kinds={"checkmate","stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"}
    is_terminal=lambda item:any(event.kind in terminal_kinds
                                for event in getattr(item,"events",()))
    is_tactic=has_review_tactical_idea
    endings=[item for item in items if is_terminal(item)]
    candidates=[item for item in items if (item.verdict in labels or is_tactic(item)) and not is_terminal(item)]
    def score(item):
        event_bonus=.07 if is_tactic(item) else 0.0
        return item.expected_loss+labels.get(item.verdict,0.0)+event_bonus
    ranked=sorted(candidates,key=lambda item:(score(item),-item.ply),reverse=True)[:limit]
    if endings:ranked=(ranked[:limit-1]+[max(endings,key=lambda item:item.ply)])
    return sorted(ranked,key=lambda item:item.ply)


def review_moment_tags(items: list[ReviewMove], limit: int = 8) -> dict[int,list[str]]:
    """Map curated summary moments to their classifications and verified events."""
    result={}
    event_tags={
        "checkmate":"Checkmate","stalemate":"Stalemate","insufficient_material":"Insufficient material",
        "seventyfive_move_rule":"75-move draw","fivefold_repetition":"Fivefold repetition",
        "missed_mate":"Missed mate","opponent_mate":"Mate allowed","back_rank_mate":"Back-rank danger",
        "missed_tactic":"Missed tactic","missed_material":"Missed material","opponent_capture":"Loose piece",
        "pv_material_loss":"Material loss in line","pv_material_gain":"Material won in line",
        "only_good_move":"Only move","turnaround":"Turnaround","sound_sacrifice":"Sound sacrifice",
        "fork":"Fork","opponent_hanging_piece":"Opponent piece left hanging",
        "skewer":"Skewer","discovered_attack":"Discovered attack",
        "discovered_check":"Discovered check","removed_defender":"Defender removed",
        "lost_defender":"Lost defender","hanging_piece":"Hanging piece","trapped_piece":"Trapped piece",
        "missed_plan":"Positional chance","king_exposure":"King exposed","pawn_structure":"Pawn weakness",
        "restricted_piece":"Restricted piece","forcing_check":"Forcing check","promotion":"Promotion",
        "king_safety":"King secured","central_control":"Center control","piece_activity":"Piece activity",
        "development":"Development","castling":"Castling",
    }
    priority={"checkmate":0,"missed_mate":1,"opponent_mate":1,"back_rank_mate":1,
              "lost_defender":2,"hanging_piece":2,"trapped_piece":2,"opponent_capture":2,
              "pv_material_loss":2,"sound_sacrifice":2,"turnaround":2,"only_good_move":2,
              "opponent_hanging_piece":3,
              "missed_tactic":3,"missed_material":3,"king_exposure":3,
              "pv_material_gain":3,"fork":4,"skewer":4,"discovered_check":4,
              "discovered_attack":4,"removed_defender":4,"new_pin":4,
              "missed_plan":5,"pawn_structure":5,"restricted_piece":5,"opponent_check":5}
    for item in rank_review_moments(items,limit):
        labels=[]
        if item.verdict in ("Brilliant","Great","Miss","Blunder","Mistake","Inaccuracy"):
            labels.append(item.verdict)
        events=getattr(item,"events",())
        candidates=sorted((event for event in events if (event.severity in ("positive","critical","warning")
                           or event.kind in {"stalemate","insufficient_material","seventyfive_move_rule","fivefold_repetition"})
                           and (event.kind in event_tags or event.kind=="new_pin")),
                          key=lambda event:priority.get(event.kind,6))
        if candidates:
            event=candidates[0]
            tag=("Checkmate" if event.kind=="checkmate" else
                 "Pin created" if event.kind=="new_pin" and event.severity=="positive" else
                 "Piece pinned" if event.kind=="new_pin" else event_tags[event.kind])
            labels.append(tag)
        if has_review_tactical_idea(item):
            if not any(label in {"Fork","Skewer","Discovered attack","Discovered check",
                                 "Defender removed","Sound sacrifice","Material won in line","Pin created"}
                       for label in labels):
                labels.append("Tactical idea")
        result[item.ply]=labels or [item.verdict]
    return result


def review_moment_impact(item: ReviewMove) -> str:
    """Show point loss only when it is a meaningful part of this moment."""
    points=max(0.0,float(item.expected_loss))*100
    return f"{points:.1f} pp expected score lost" if points>=.05 else ""


def annotate_review_pgn(game: chess.pgn.Game, moves: list[chess.Move], items: list[ReviewMove],
                        user_color: Optional[chess.Color] = None) -> chess.pgn.Game:
    """Attach review comments and best-move side variations to a PGN game."""
    node=game;board=game.board()
    for index,move in enumerate(moves):
        item=items[index] if index<len(items) else None
        parent=node
        node=parent.add_variation(move)
        if item and item.best_move in board.legal_moves and item.best_move!=move:
            variation=parent.add_variation(item.best_move,comment=f"Engine choice: {item.best_san}")
            board.push(item.best_move)
            for pv_move in item.best_pv_moves[1:]:
                if pv_move not in board.legal_moves:break
                board.push(pv_move)
                if pv_move not in variation.board().legal_moves:break
                variation=variation.add_variation(pv_move)
            board=parent.board()
        if item:
            comment=review_text_for_viewer(item.explanation.strip(),item,user_color)
            if not comment.casefold().startswith(item.verdict.casefold()+":"):
                comment=f"{item.verdict}: {comment}"
            parts=[comment]
            if item.best_san and item.best_san!=item.san:parts.append(f"Engine choice: {item.best_san}")
            if item.pv:parts.append(f"Principal variation: {item.pv}")
            node.comment=" ".join(parts)
        board.push(move)
    return game

__all__ = [
    'NEAR_BEST_EXPECTED_POINTS_TOLERANCE',
    'BRILLIANT_MAX_PREMOVE_EXPECTED_POINTS',
    'BRILLIANT_MIN_POSTMOVE_EXPECTED_POINTS',
    'BRILLIANT_MAX_EXPECTED_POINTS_LOSS',
    'BRILLIANT_MIN_REPORTED_GAP',
    'BRILLIANT_MIN_UNIQUENESS_GAP',
    'REVIEW_ANALYSIS_VERSION',
    'ACCURACY_CALIBRATION_EXPONENT',
    'GREAT_ONLY_MAX_PREMOVE_EXPECTED_POINTS',
    'GREAT_ONLY_MIN_POSTMOVE_EXPECTED_POINTS',
    'GREAT_ONLY_MIN_ALTERNATIVE_GAP',
    'VALUES',
    'eval_cp',
    'engine_line_forces_mate',
    'eval_text',
    'evaluation_label',
    'expected_points',
    'MOVE_QUALITY_TOOLTIP',
    'REVIEW_IMPACT_TOOLTIP',
    'raw_move_accuracy',
    'calibrate_accuracy',
    'uncalibrate_accuracy',
    'move_accuracy',
    'review_accuracy',
    'format_review_impact',
    'count_label',
    'normalized_move_loss',
    'override_review_category',
    'classify',
    'review_verdict_tooltip',
    'near_best_moves',
    'ReviewEvent',
    'ReviewMove',
    'TACTICAL_REVIEW_EVENT_KINDS',
    'TACTICAL_REVIEW_VERDICTS',
    'has_review_tactical_idea',
    'normalized_syzygy_path',
    'serialize_review_moves',
    'deserialize_review_moves',
    'review_move_notation',
    'review_text_for_viewer',
    'parse_review_san_line',
    'review_spoken_text',
    'review_game_narrative',
    'game_phase',
    'castling_timing_label',
    'describe_move',
    'material_balance',
    'captured_piece_details',
    'legal_capture_map',
    'legal_defender_map',
    'skewer_lines',
    'newly_created_skewers',
    'fork_targets',
    'tactical_themes',
    'newly_opened_slider_targets',
    'newly_pinned_pieces',
    'relative_pin_lines',
    'newly_relative_pins',
    'piece_label',
    'missed_plan_event',
    'removed_defender_event',
    'is_engine_justified_sacrifice',
    'brilliant_choice_gap',
    'can_label_brilliant',
    'only_good_move_gap',
    'analyze_review_events',
    'review_reflection_prompt',
    'review_focus_squares',
    'review_practice_hint',
    'generate_review_commentary',
    'game_coach_summary',
    'recurring_review_themes',
    'recurring_review_strengths',
    'personalized_review_coach',
    'player_review_overview',
    'review_filter_indices',
    'review_row_ply',
    'review_practice_moves',
    'rank_review_moments',
    'review_moment_tags',
    'review_moment_impact',
    'annotate_review_pgn',
]
