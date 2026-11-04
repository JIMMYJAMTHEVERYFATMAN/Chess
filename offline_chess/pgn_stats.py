"""Fast, engine-free facts derived from saved PGN mainlines."""
from __future__ import annotations

from collections import Counter
import json
import re

import chess
import chess.pgn

PGN_STATS_VERSION = "2"
SIDES = ("White", "Black")
PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}
SIMPLE_TIME_CONTROL = re.compile(r"^(\d+(?:\.\d+)?)(?:\+(\d+(?:\.\d+)?))?$")


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
    checks = Counter({side: 0 for side in SIDES})
    captures = Counter({side: 0 for side in SIDES})
    captured = {side: Counter() for side in SIDES}
    promotions = Counter({side: 0 for side in SIDES})
    promotion_pieces = {side: Counter() for side in SIDES}
    castling_ply = {side: None for side in SIDES}
    clocks = Counter({side: 0 for side in SIDES})
    elapsed = Counter({side: 0 for side in SIDES})
    elapsed_sources = {side: Counter() for side in SIDES}
    last_clock = {side: initial_clock for side in SIDES}
    clock_moves = Counter({side: 0 for side in SIDES})
    explicit_since_clock = Counter({side: 0.0 for side in SIDES})
    explicit_moves_since_clock = Counter({side: 0 for side in SIDES})
    positions = Counter({_position_key(board): 1})
    repeated_positions = 0
    queenless_ply = 0 if not (board.pieces(chess.QUEEN, chess.WHITE) or board.pieces(chess.QUEEN, chess.BLACK)) else None
    plies = 0

    for node in game.mainline():
        move = node.move
        side = "White" if board.turn == chess.WHITE else "Black"
        moves[side] += 1
        piece = board.piece_type_at(move.from_square)
        if piece in PIECE_NAMES:
            moves_by_piece[side][PIECE_NAMES[piece]] += 1
        plies += 1
        if board.is_castling(move) and castling_ply[side] is None:
            castling_ply[side] = plies
        if board.is_capture(move):
            captures[side] += 1
            victim_square = move.to_square
            if board.is_en_passant(move):
                victim_square += -8 if board.turn == chess.WHITE else 8
            victim = board.piece_type_at(victim_square)
            if victim in PIECE_NAMES:
                captured[side][PIECE_NAMES[victim]] += 1
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
        if board.is_check():
            checks[side] += 1
        if queenless_ply is None and not (board.pieces(chess.QUEEN, chess.WHITE) or board.pieces(chess.QUEEN, chess.BLACK)):
            queenless_ply = plies
        key = _position_key(board)
        positions[key] += 1
        if positions[key] == 2:
            repeated_positions += 1

    return {
        "source": "pgn", "version": PGN_STATS_VERSION, "plies": plies,
        "moves": dict(moves), "moves_by_piece": {side: dict(moves_by_piece[side]) for side in SIDES},
        "checks": dict(checks), "captures": dict(captures),
        "captured_pieces": {side: dict(captured[side]) for side in SIDES},
        "promotions": dict(promotions),
        "promotion_pieces": {side: dict(promotion_pieces[side]) for side in SIDES},
        "castling_ply": castling_ply,
        "queenless_ply": queenless_ply, "repeated_positions": repeated_positions,
        "clock_annotations": dict(clocks), "elapsed_annotations": {
            side: elapsed[side] for side in SIDES
        }, "elapsed_seconds": {side: elapsed[f"{side}_seconds"] for side in SIDES},
        "elapsed_sources": {side: dict(elapsed_sources[side]) for side in SIDES},
        "final_position": ("checkmate" if board.is_checkmate() else
                           "stalemate" if board.is_stalemate() else "ongoing_or_other"),
    }


def aggregate_pgn_stats(records):
    """Build display-ready measures with an explicit valid-PGN denominator."""
    paired = []
    errors = 0
    for record in records:
        try:
            value = json.loads(record.get("pgn_stats_json") or "{}")
        except (TypeError, ValueError):
            value = {}
        if isinstance(value, dict) and value.get("source") == "pgn" and value.get("version") == PGN_STATS_VERSION:
            if value.get("error"):
                errors += 1
            else:
                paired.append((record, value))
    valid = [value for _, value in paired]
    total = len(records)
    scanned = len(valid) + errors
    denominator = len(valid)
    if not denominator:
        return {"scanned": scanned, "total": total, "valid": 0, "errors": errors,
                "pending": max(0, total - scanned), "rows": []}

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
    queenless = sum(game.get("queenless_ply") is not None for game in valid)
    promoted = sum(sum(game.get("promotions", {}).values()) > 0 for game in valid)
    user_elapsed_sources = Counter()
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
            "pending": pending, "rows": rows}


__all__ = ["PGN_STATS_VERSION", "extract_pgn_stats", "aggregate_pgn_stats"]
