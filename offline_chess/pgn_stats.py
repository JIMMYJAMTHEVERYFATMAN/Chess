"""Fast, engine-free facts derived from saved PGN mainlines."""
from __future__ import annotations

from collections import Counter
import json
import math
import re

import chess
import chess.pgn

PGN_STATS_VERSION = "3"
SIDES = ("White", "Black")
PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}
SIMPLE_TIME_CONTROL = re.compile(r"^(\d+(?:\.\d+)?)(?:\+(\d+(?:\.\d+)?))?$")
OPENING_MOVE_LIMIT = 10
CLOCK_TROUBLE_SECONDS = 30


def _position_key(board):
    """Use repetition-relevant FEN fields and ignore move counters."""
    return " ".join(board.fen(en_passant="legal").split(" ")[:4])


def extract_pgn_stats(game, time_control=""):
    """Summarize a parsed PGN in one mainline pass; never invoke an engine."""
    board = game.board()
    control = SIMPLE_TIME_CONTROL.fullmatch(str(time_control or game.headers.get("TimeControl", "")).strip())
    initial_clock = float(control.group(1)) if control else None
    increment = float(control.group(2) or 0) if control else 0.0
    moves = Counter({side: 0 for side in SIDES})
    moves_by_piece = {side: Counter() for side in SIDES}
    piece_ids = {square: square for square in board.piece_map()}
    piece_origins = dict(piece_ids)
    initial_minor_ids = {side: set() for side in SIDES}
    for square,piece in board.piece_map().items():
        if piece.piece_type in (chess.KNIGHT,chess.BISHOP):
            initial_minor_ids["White" if piece.color==chess.WHITE else "Black"].add(square)
    opening_queen_moves = Counter({side: 0 for side in SIDES})
    opening_repeated_piece_moves = Counter({side: 0 for side in SIDES})
    opening_seen_ids = {side: set() for side in SIDES}
    opening_developed = {side: None for side in SIDES}
    opening_castled = {side: False for side in SIDES}
    checks = Counter({side: 0 for side in SIDES})
    captures = Counter({side: 0 for side in SIDES})
    captured = {side: Counter() for side in SIDES}
    promotions = Counter({side: 0 for side in SIDES})
    promotion_pieces = {side: Counter() for side in SIDES}
    castling_ply = {side: None for side in SIDES}
    clock_at_move = {side: {} for side in SIDES}
    low_clock_moves = Counter({side: 0 for side in SIDES})
    clocks = Counter({side: 0 for side in SIDES})
    elapsed = Counter({side: 0 for side in SIDES})
    elapsed_sources = {side: Counter() for side in SIDES}
    last_clock = {side: initial_clock for side in SIDES}
    clock_moves = Counter({side: 0 for side in SIDES})
    explicit_since_clock = Counter({side: 0.0 for side in SIDES})
    explicit_moves_since_clock = Counter({side: 0 for side in SIDES})
    positions = Counter({_position_key(board): 1})
    repeated_positions = 0
    threefold_positions = 0
    queenless_ply = 0 if not (board.pieces(chess.QUEEN, chess.WHITE) or board.pieces(chess.QUEEN, chess.BLACK)) else None
    rookless_ply = None
    pawn_endgame_ply = None
    immediate_recaptures = Counter({side: 0 for side in SIDES})
    previous_capture_square = None
    previous_capture_side = None
    draw_reason = "Not a draw"
    if str(game.headers.get("Result", "")) in ("1/2-1/2", "½-½"):
        termination=str(game.headers.get("Termination", "")).casefold()
        if "repetition" in termination:draw_reason="Repetition"
        elif "50 move" in termination or "fifty" in termination:draw_reason="50-move rule"
        elif "stalemate" in termination:draw_reason="Stalemate"
        elif "insufficient" in termination:draw_reason="Insufficient material"
        elif "agreement" in termination:draw_reason="Agreement"
        elif "timeout" in termination or "time" in termination:draw_reason="Timeout"
        else:draw_reason="Other / unspecified"
    plies = 0

    for node in game.mainline():
        move = node.move
        side = "White" if board.turn == chess.WHITE else "Black"
        moves[side] += 1
        piece = board.piece_type_at(move.from_square)
        piece_id=piece_ids.get(move.from_square)
        castle_rook_move=None
        if board.is_castling(move):
            rank=chess.square_rank(move.from_square)
            kingside=move.to_square>move.from_square
            rook_from=chess.square(7 if kingside else 0,rank)
            rook_to=chess.square(5 if kingside else 3,rank)
            castle_rook_move=(piece_ids.pop(rook_from,None),rook_to)
        if piece in PIECE_NAMES:
            moves_by_piece[side][PIECE_NAMES[piece]] += 1
        plies += 1
        side_move=moves[side]
        if side_move<=OPENING_MOVE_LIMIT and piece_id is not None:
            if piece==chess.QUEEN:opening_queen_moves[side]+=1
            if piece_id in opening_seen_ids[side]:opening_repeated_piece_moves[side]+=1
            opening_seen_ids[side].add(piece_id)
        if board.is_castling(move) and castling_ply[side] is None:
            castling_ply[side] = plies
        if board.is_castling(move) and side_move<=OPENING_MOVE_LIMIT:
            opening_castled[side]=True
        if board.is_capture(move):
            captures[side] += 1
            victim_square = move.to_square
            if board.is_en_passant(move):
                victim_square += -8 if board.turn == chess.WHITE else 8
            victim = board.piece_type_at(victim_square)
            if victim in PIECE_NAMES:
                captured[side][PIECE_NAMES[victim]] += 1
            if previous_capture_square==victim_square and previous_capture_side!=side:
                immediate_recaptures[side]+=1
            piece_ids.pop(victim_square,None)
            previous_capture_square=move.to_square
            previous_capture_side=side
        else:
            previous_capture_square=None
            previous_capture_side=None
        if move.promotion:
            promotions[side] += 1
            promotion_pieces[side][PIECE_NAMES[move.promotion]] += 1
        move_time = node.emt()
        if move_time is not None:
            elapsed[side] += 1
            elapsed[f"{side}_seconds"] += max(0.0, float(move_time))
            elapsed_sources[side]["emt"] += 1
            explicit_since_clock[side] += max(0.0, float(move_time))
            explicit_moves_since_clock[side] += 1
        clock_moves[side] += 1
        clock = node.clock()
        if clock is not None:
            clocks[side] += 1
            if side_move in (10,20,30):clock_at_move[side][str(side_move)]=float(clock)
            if float(clock)<=CLOCK_TROUBLE_SECONDS:low_clock_moves[side]+=1
            previous_clock = last_clock[side]
            unmeasured_moves = clock_moves[side] - explicit_moves_since_clock[side]
            if previous_clock is not None and unmeasured_moves > 0:
                elapsed_from_clock = previous_clock + increment * clock_moves[side] - float(clock)
                inferred_seconds = elapsed_from_clock - explicit_since_clock[side]
                if inferred_seconds >= -0.5:
                    elapsed[side] += unmeasured_moves
                    elapsed[f"{side}_seconds"] += max(0.0, inferred_seconds)
                    elapsed_sources[side]["clock"] += unmeasured_moves
            last_clock[side] = float(clock)
            clock_moves[side] = 0
            explicit_since_clock[side] = 0.0
            explicit_moves_since_clock[side] = 0
        board.push(move)
        if piece_id is not None:
            piece_ids.pop(move.from_square,None)
            piece_ids[move.to_square]=piece_id
        if castle_rook_move and castle_rook_move[0] is not None:
            piece_ids[castle_rook_move[1]]=castle_rook_move[0]
        if board.is_check():
            checks[side] += 1
        if side_move==OPENING_MOVE_LIMIT:
            opening_developed[side]=sum(
                1 for initial_id in initial_minor_ids[side]
                if initial_id in piece_ids.values()
                and next((sq for sq,pid in piece_ids.items() if pid==initial_id),None)!=piece_origins[initial_id]
            )
        key = _position_key(board)
        positions[key] += 1
        if positions[key] == 2:
            repeated_positions += 1
        elif positions[key] == 3:
            threefold_positions += 1
        if queenless_ply is None and not (board.pieces(chess.QUEEN, chess.WHITE) or board.pieces(chess.QUEEN, chess.BLACK)):
            queenless_ply = plies
        if rookless_ply is None and not (board.pieces(chess.QUEEN,chess.WHITE) or board.pieces(chess.QUEEN,chess.BLACK)
                                         or board.pieces(chess.ROOK,chess.WHITE) or board.pieces(chess.ROOK,chess.BLACK)):
            rookless_ply=plies
        if pawn_endgame_ply is None and not any(board.pieces(piece_type,color)
                                                for piece_type in (chess.QUEEN,chess.ROOK,chess.BISHOP,chess.KNIGHT)
                                                for color in (chess.WHITE,chess.BLACK)):
            pawn_endgame_ply=plies

    return {
        "source": "pgn", "version": PGN_STATS_VERSION, "plies": plies,
        "moves": dict(moves), "moves_by_piece": {side: dict(moves_by_piece[side]) for side in SIDES},
        "opening_development": {side: {
            "minor_pieces_by_move_10": opening_developed[side],
            "queen_moves_through_move_10": opening_queen_moves[side],
            "repeated_piece_moves_through_move_10": opening_repeated_piece_moves[side],
            "castled_by_move_10": opening_castled[side],
        } for side in SIDES},
        "checks": dict(checks), "captures": dict(captures),
        "captured_pieces": {side: dict(captured[side]) for side in SIDES},
        "promotions": dict(promotions),
        "promotion_pieces": {side: dict(promotion_pieces[side]) for side in SIDES},
        "castling_ply": castling_ply,"clock_at_move":clock_at_move,
        "low_clock_moves":dict(low_clock_moves),"immediate_recaptures":dict(immediate_recaptures),
        "queenless_ply": queenless_ply,"rookless_ply":rookless_ply,"pawn_endgame_ply":pawn_endgame_ply,
        "draw_reason":draw_reason,"repeated_positions": repeated_positions,"threefold_positions":threefold_positions,
        "clock_annotations": dict(clocks), "elapsed_annotations": {
            side: elapsed[side] for side in SIDES
        }, "elapsed_seconds": {side: elapsed[f"{side}_seconds"] for side in SIDES},
        "elapsed_sources": {side: dict(elapsed_sources[side]) for side in SIDES},
        "final_position": ("checkmate" if board.is_checkmate() else
                           "stalemate" if board.is_stalemate() else "ongoing_or_other"),
    }


def _valid_cached_summary(value):
    """Reject malformed persisted summaries before they reach profile aggregation."""
    def numeric(item):
        if not isinstance(item, (int, float)):
            return False
        try:
            return math.isfinite(float(item))
        except OverflowError:
            return False

    for field in ("plies", "repeated_positions", "threefold_positions"):
        if field in value and not numeric(value[field]):
            return False
    for field in ("queenless_ply","rookless_ply","pawn_endgame_ply"):
        if field in value and value[field] is not None and not numeric(value[field]):
            return False
    for field in ("moves", "checks", "captures", "promotions", "clock_annotations",
                  "elapsed_annotations", "elapsed_seconds"):
        mapping = value.get(field, {})
        if not isinstance(mapping, dict) or any(not numeric(item) for item in mapping.values()):
            return False
    for field in ("moves_by_piece", "captured_pieces", "promotion_pieces", "elapsed_sources",
                  "low_clock_moves","immediate_recaptures"):
        sides = value.get(field, {})
        if not isinstance(sides, dict):
            return False
        for piece_counts in sides.values():
            if not isinstance(piece_counts, dict) or any(not numeric(item) for item in piece_counts.values()):
                return False
    castling = value.get("castling_ply", {})
    if not isinstance(castling, dict) or any(item is not None and not numeric(item)
                                               for item in castling.values()):
        return False
    clocks=value.get("clock_at_move",{})
    if not isinstance(clocks,dict):return False
    for side_times in clocks.values():
        if not isinstance(side_times,dict) or any(not numeric(item) for item in side_times.values()):return False
    development=value.get("opening_development",{})
    if not isinstance(development,dict):return False
    for side_data in development.values():
        if not isinstance(side_data,dict):return False
        for field in ("minor_pieces_by_move_10","queen_moves_through_move_10",
                      "repeated_piece_moves_through_move_10"):
            item=side_data.get(field)
            if item is not None and not numeric(item):return False
        if not isinstance(side_data.get("castled_by_move_10"),bool):return False
    if not isinstance(value.get("draw_reason",""),str):return False
    return isinstance(value.get("final_position", ""), str)


def aggregate_pgn_stats(records):
    """Build display-ready measures with an explicit valid-PGN denominator."""
    paired = []
    errors = 0
    for record in records:
        if not isinstance(record, dict):
            errors += 1
            continue
        source = record.get("pgn_stats_json")
        try:
            value = json.loads(source or "{}")
        except (TypeError, ValueError):
            if source:
                errors += 1
            value = {}
        if isinstance(value, dict) and value.get("source") == "pgn" and value.get("version") == PGN_STATS_VERSION:
            if value.get("error"):
                errors += 1
            elif not _valid_cached_summary(value):
                errors += 1
            else:
                paired.append((record, value))
    valid = [value for _, value in paired]
    total = len(records)
    scanned = len(valid) + errors
    denominator = len(valid)
    if not denominator:
        return {"scanned": scanned, "total": total, "valid": 0, "errors": errors,
                "pending": max(0, total - scanned),
                "rows": [("Unreadable PGNs", f"{errors:,}", f"of {total:,} attempted")] if errors else []}

    def mean(field):
        return sum(int(game.get(field) or 0) for game in valid) / denominator

    user_pairs = [(record, stats) for record, stats in paired if record.get("user_color") in SIDES]
    user_denominator = len(user_pairs)

    def user_total(field, opponent=False):
        total = 0
        for record, stats in user_pairs:
            side = record["user_color"]
            if opponent:
                side = "Black" if side == "White" else "White"
            total += int(stats.get(field, {}).get(side) or 0)
        return total

    def user_mean(field, opponent=False):
        return user_total(field, opponent) / user_denominator if user_denominator else 0

    castle_games = [stats.get("castling_ply", {}).get(record["user_color"])
                    for record, stats in user_pairs]
    castle_games = [ply for ply in castle_games if ply is not None]
    repeated = sum(bool(game.get("repeated_positions")) for game in valid)
    threefold = sum(bool(game.get("threefold_positions")) for game in valid)
    queenless = sum(game.get("queenless_ply") is not None for game in valid)
    promoted = sum(sum(game.get("promotions", {}).values()) > 0 for game in valid)
    user_elapsed_sources = Counter()
    sections={}
    if user_denominator:
        user_clock_moves = user_total("clock_annotations")
        user_clock_games = sum(bool(stats.get("clock_annotations", {}).get(record["user_color"], 0))
                               for record, stats in user_pairs)
        user_elapsed_moves = user_total("elapsed_annotations")
        user_elapsed_seconds = sum(float(stats.get("elapsed_seconds", {}).get(record["user_color"], 0))
                                   for record, stats in user_pairs)
        captured_counts = Counter()
        user_piece_moves = Counter()
        promoted_pieces = Counter()
        for record, stats in user_pairs:
            side = record["user_color"]
            captured_counts.update(stats.get("captured_pieces", {}).get(side, {}))
            user_piece_moves.update(stats.get("moves_by_piece", {}).get(side, {}))
            promoted_pieces.update(stats.get("promotion_pieces", {}).get(side, {}))
            user_elapsed_sources.update(stats.get("elapsed_sources", {}).get(side, {}))
        captured_detail = " · " + ", ".join(f"{piece} {captured_counts[piece]}" for piece in PIECE_NAMES.values() if captured_counts[piece]) if captured_counts else ""
        piece_move_detail = ", ".join(
            f"{piece} {user_piece_moves[piece] / user_denominator:.1f}" for piece in PIECE_NAMES.values()
            if user_piece_moves[piece]) or "none"
        promotion_detail = " · " + ", ".join(
            f"{piece} {promoted_pieces[piece]}" for piece in PIECE_NAMES.values() if promoted_pieces[piece]) if promoted_pieces else ""
        clock_samples=Counter();clock_seconds=Counter();low_clock_count=0
        opening_samples=0;developed_total=queen_moves_total=repeated_opening_total=early_castles=0
        user_immediate_recaptures=opponent_immediate_recaptures=0
        user_nonpawn_captures=opponent_nonpawn_captures=0
        for record,stats in user_pairs:
            side=record["user_color"];other="Black" if side=="White" else "White"
            for move_no,seconds in stats.get("clock_at_move",{}).get(side,{}).items():
                clock_samples[move_no]+=1;clock_seconds[move_no]+=float(seconds)
            low_clock_count+=int(stats.get("low_clock_moves",{}).get(side,0))
            development=stats.get("opening_development",{}).get(side,{})
            developed=development.get("minor_pieces_by_move_10")
            if developed is not None:
                opening_samples+=1;developed_total+=int(developed)
                queen_moves_total+=int(development.get("queen_moves_through_move_10",0))
                repeated_opening_total+=int(development.get("repeated_piece_moves_through_move_10",0))
                early_castles+=bool(development.get("castled_by_move_10",False))
            recaptures=stats.get("immediate_recaptures",{})
            user_immediate_recaptures+=int(recaptures.get(side,0))
            opponent_immediate_recaptures+=int(recaptures.get(other,0))
            captured_by_side=stats.get("captured_pieces",{})
            user_nonpawn_captures+=sum(int(count) for piece,count in captured_by_side.get(side,{}).items()
                                       if piece!="pawn")
            opponent_nonpawn_captures+=sum(int(count) for piece,count in captured_by_side.get(other,{}).items()
                                           if piece!="pawn")

        user_clock_moves=sum(int(stats.get("clock_annotations",{}).get(record["user_color"],0))
                             for record,stats in user_pairs)
        time_rows=[(f"Clock left after move {move_no}",
                    f"{clock_seconds[move_no]/clock_samples[move_no]:.1f} sec" if clock_samples[move_no] else "Not available",
                    f"{clock_samples[move_no]:,} games") for move_no in ("10","20","30")]
        time_rows.append((f"Moves with ≤{CLOCK_TROUBLE_SECONDS}s left",
                          f"{low_clock_count:,} · {low_clock_count/user_clock_moves*100:.1f}%" if user_clock_moves else "Not available",
                          f"{user_clock_moves:,} annotated moves"))
        sections["time"]=time_rows
        sections["development"]=[
            ("Minor pieces developed by your move 10",f"{developed_total/opening_samples:.2f} of 4" if opening_samples else "Not available",
             f"{opening_samples:,} games reaching 10 moves with known positions"),
            ("Queen moves through your move 10",f"{queen_moves_total/opening_samples:.2f} per game" if opening_samples else "Not available",
             f"{opening_samples:,} games"),
            ("Repeated piece moves through your move 10",f"{repeated_opening_total/opening_samples:.2f} per game" if opening_samples else "Not available",
             f"{opening_samples:,} games"),
            ("Castled by your move 10",f"{early_castles:,} · {early_castles/opening_samples*100:.1f}%" if opening_samples else "Not available",
             f"{opening_samples:,} games"),
        ]
        queenless_plies=[int(stats["queenless_ply"]) for _record,stats in paired
                         if stats.get("queenless_ply") is not None]
        sections["trades"]=[
            ("Your captures of non-pawns",f"{user_nonpawn_captures:,}",f"{user_denominator:,} games"),
            ("Opponent captures of non-pawns",f"{opponent_nonpawn_captures:,}",f"{user_denominator:,} games"),
            ("Your immediate recaptures",f"{user_immediate_recaptures:,}",f"{user_denominator:,} games"),
            ("Opponent immediate recaptures",f"{opponent_immediate_recaptures:,}",f"{user_denominator:,} games"),
            ("Reached a queenless position",f"{len(queenless_plies):,} · avg move {sum(queenless_plies)/len(queenless_plies)/2:.1f}" if queenless_plies else "0",
             f"{denominator:,} valid PGNs"),
        ]
        draw_pairs=[(record,stats) for record,stats in paired if record.get("user_result")==.5]
        draw_reasons=Counter(stats.get("draw_reason","Other / unspecified") for _record,stats in draw_pairs)
        sections["draws"]=[
            ("Drawn games",f"{len(draw_pairs):,}",f"{denominator:,} valid PGNs"),
            ("Draws by agreement",f"{draw_reasons['Agreement']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Draws by repetition",f"{draw_reasons['Repetition']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Draws by 50-move rule",f"{draw_reasons['50-move rule']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Draws by stalemate",f"{draw_reasons['Stalemate']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Draws by insufficient material",f"{draw_reasons['Insufficient material']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Draws by timeout",f"{draw_reasons['Timeout']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Other / unspecified draw reason",f"{draw_reasons['Other / unspecified']:,}",f"{len(draw_pairs):,} drawn games"),
            ("Games with a 3+ occurrence position",f"{threefold:,}",f"{denominator:,} valid PGNs"),
        ]
        def endgame_row(label,field):
            reached=[int(stats[field]) for _record,stats in paired if stats.get(field) is not None]
            return (label,f"{len(reached):,} · avg move {sum(reached)/len(reached)/2:.1f}" if reached else "0",
                    f"{denominator:,} valid PGNs")
        sections["endgames"]=[
            endgame_row("Reached a queenless position","queenless_ply"),
            endgame_row("Reached a position without queens or rooks","rookless_ply"),
            endgame_row("Reached a kings-and-pawns-only position","pawn_endgame_ply"),
        ]
    terminal_games = Counter(game.get("final_position") for game in valid)
    if user_elapsed_sources.get("clock") and user_elapsed_sources.get("emt"):
        timing_coverage = "Clock-derived from [%clk] and time control; [%emt] used when present"
    elif user_elapsed_sources.get("clock"):
        timing_coverage = "Derived from [%clk] and the PGN time control"
    elif user_elapsed_sources.get("emt"):
        timing_coverage = "Read from [%emt] comments"
    else:
        timing_coverage = "Timing annotations not provided in these PGNs"
    rows = [
        ("Average game length", f"{mean('plies') / 2:.1f} moves", f"{denominator:,}/{total:,} PGNs"),
        ("Checks delivered", f"{sum(sum(g.get('checks', {}).values()) for g in valid) / denominator:.1f} per game", f"{denominator:,} valid PGNs"),
        ("Your checks", f"{user_mean('checks'):.1f} per game" if user_denominator else "—", f"{user_denominator:,} games with known color"),
        ("Your moves by piece", piece_move_detail if user_denominator else "—", f"Average per game · {user_denominator:,} games with known color"),
        ("Your captures", f"{user_mean('captures'):.1f} per game{captured_detail}" if user_denominator else "—", f"{user_denominator:,} games with known color"),
        ("Opponent captures", f"{user_mean('captures', opponent=True):.1f} per game" if user_denominator else "—", f"{user_denominator:,} games with known color"),
        ("Your castling", f"{len(castle_games):,}/{user_denominator:,} games" + (f" · avg ply {sum(castle_games) / len(castle_games):.1f}" if castle_games else "") if user_denominator else "—", f"{user_denominator:,} games with known color"),
        ("Your promotions", f"{user_total('promotions'):,}{promotion_detail}" if user_denominator else f"{promoted:,} games had a promotion", f"{user_denominator:,} games with known color" if user_denominator else f"{denominator:,} valid PGNs"),
        ("Queenless positions reached", f"{queenless:,}/{denominator:,}", f"{denominator:,} valid PGNs"),
        ("Games with a repeated position", f"{repeated:,}/{denominator:,}", f"{denominator:,} valid PGNs"),
        ("PGN final position", f"{terminal_games['checkmate']:,} checkmate · {terminal_games['stalemate']:,} stalemate", f"{denominator:,} valid PGNs"),
        ("Your clock annotations", f"{user_clock_moves:,} moves in {user_clock_games:,} games" if user_denominator and user_clock_moves else "Not provided", "Only where Chess.com included [%clk]"),
        ("Your time per move", f"{user_elapsed_seconds / user_elapsed_moves:.1f} sec/move · {user_elapsed_moves:,} moves" if user_denominator and user_elapsed_moves else "Not provided", timing_coverage),
    ]
    if errors:
        rows.append(("Unreadable PGNs", f"{errors:,}", f"of {total:,} attempted"))
    pending = max(0, total - scanned)
    return {"scanned": scanned, "total": total, "valid": denominator, "errors": errors,
            "pending": pending, "rows": rows,"sections":sections}


__all__ = ["PGN_STATS_VERSION", "extract_pgn_stats", "aggregate_pgn_stats"]
