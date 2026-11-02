"""Lichess opening catalog loading and local opening recognition."""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn


@dataclass
class Opening:
    eco: str
    name: str
    sans: tuple[str, ...]
    position_keys: tuple[str, ...]


def position_key(board: chess.Board) -> str:
    """Return a position identity without move counters."""
    fields = board.fen().split()
    return " ".join(fields[:4])


def parse_pgn_line(text: str) -> list[chess.Move]:
    """Parse Lichess's PGN-formatted third TSV column into legal moves."""
    game = chess.pgn.read_game(io.StringIO(text.strip()))
    if game is not None and game.errors:
        raise ValueError(str(game.errors[0]))
    return list(game.mainline_moves()) if game is not None else []


def opening_from_file(path: Path, position_key_cache=None) -> list[Opening]:
    if position_key_cache is None:
        position_key_cache = {}
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                continue
            eco, name, pgn = (cell.strip() for cell in row[:3])
            if eco.lower() == "eco" and name.lower() == "name":
                continue
            if not eco or not name or not pgn:
                continue
            try:
                moves = parse_pgn_line(pgn)
                board = chess.Board()
                sans, keys = [], []
                for move in moves:
                    if move not in board.legal_moves:
                        raise ValueError("illegal move in opening line")
                    sans.append(board.san(move))
                    board.push(move)
                    cache_key = (board.chess960, board._transposition_key())
                    key = position_key_cache.get(cache_key)
                    if key is None:
                        key = position_key(board)
                        position_key_cache[cache_key] = key
                    keys.append(key)
                if sans:
                    result.append(Opening(eco, name, tuple(sans), tuple(keys)))
            except (ValueError, AssertionError):
                continue
    return result


def load_openings(paths: list[Path]) -> list[Opening]:
    all_openings = []
    position_key_cache = {}
    for path in paths:
        try:
            all_openings.extend(opening_from_file(path, position_key_cache))
        except (OSError, UnicodeError, csv.Error):
            continue
    return all_openings


def discover_lichess_files(folder: Path) -> list[Path]:
    return [folder / f"{letter}.tsv" for letter in "abcde" if (folder / f"{letter}.tsv").is_file()]


def detect_opening(moves: list[chess.Move], openings: list[Opening]) -> str:
    if not openings:
        return "Opening database not loaded"
    by_position = {}
    for opening in openings:
        for ply, key in enumerate(opening.position_keys, 1):
            current = by_position.get(key)
            if current is None or ply > current[0]:
                by_position[key] = (ply, opening)
    board = chess.Board()
    match = None
    for ply, move in enumerate(moves, 1):
        if move not in board.legal_moves:
            break
        board.push(move)
        item = by_position.get(position_key(board))
        if item and (match is None or ply >= match[0]):
            match = (ply, item[1])
    if match:
        op = match[1]
        return f"{op.name} · {op.eco}"
    return "Opening not found in local database"


def find_opening_ply(moves: list[chess.Move], openings: list[Opening]) -> int:
    known_positions = {key for opening in openings for key in opening.position_keys}
    if not known_positions:
        return 0
    board = chess.Board()
    last = 0
    for ply, move in enumerate(moves, 1):
        if move not in board.legal_moves:
            break
        board.push(move)
        if position_key(board) in known_positions:
            last = ply
    return last


def local_opening_exit_depth(pgn_path, user_color, book_positions):
    """Count a player's book moves when their move is the first local-book exit."""
    if not book_positions:
        return None
    try:
        with Path(pgn_path).open("r", encoding="utf-8-sig") as source:
            game = chess.pgn.read_game(source)
        if game is None or game.errors or game.board().chess960:
            return None
        board = game.board()
        user_book_moves = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                break
            mover = "White" if board.turn == chess.WHITE else "Black"
            was_book = position_key(board) in book_positions
            board.push(move)
            remains_book = position_key(board) in book_positions
            if remains_book:
                if mover == user_color:
                    user_book_moves += 1
                continue
            if was_book and mover == user_color:
                return user_book_moves
            if was_book:
                return None
        return None
    except (OSError, UnicodeError, ValueError):
        return None
