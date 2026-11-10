"""Chess.com archive access, game metadata, and local profile storage."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import chess
import chess.engine
import chess.pgn
from PyQt6.QtCore import QThread, pyqtSignal

from .openings import Opening, discover_lichess_files
from .review_logic import (
    ACCURACY_MODEL_VERSION,
    ACCURACY_CALIBRATION_EXPONENT,
    REVIEW_ANALYSIS_VERSION,
    VALUES,
    calibrate_accuracy,
    game_phase,
    raw_move_accuracy,
)
from .pgn_stats import PGN_STATS_VERSION, extract_pgn_stats

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

CHESSCOM_API_ROOT="https://api.chess.com/pub"
CHESSCOM_ECO_PATTERN=re.compile(r"^[A-E]\d{2}$",re.IGNORECASE)
CASTLING_SCAN_VERSION=2


def _opening_name_key(value):
    return re.sub(r"[^a-z0-9]+","",str(value or "").casefold())


@lru_cache(maxsize=1)
def chesscom_opening_catalog():
    """Index bundled Lichess opening names by ECO and normalized display name."""
    by_eco={};by_name={}
    for path in discover_lichess_files(PACKAGE_ROOT/"openings"):
        try:
            with path.open("r",encoding="utf-8-sig",newline="") as source:
                for row in csv.DictReader(source,delimiter="\t"):
                    code=str(row.get("eco","")).strip().upper()
                    name=str(row.get("name","")).strip()
                    if not CHESSCOM_ECO_PATTERN.fullmatch(code) or not name:continue
                    pgn=str(row.get("pgn","")).strip()
                    try:
                        opening=chess.pgn.read_game(io.StringIO(pgn))
                        plies=len(list(opening.mainline_moves())) if opening else 10_000
                    except (ValueError,TypeError):plies=10_000
                    entry=(plies,name)
                    by_eco.setdefault(code,[]).append(entry)
                    by_name.setdefault(_opening_name_key(name),[]).append((code,name))
        except (OSError,UnicodeError,csv.Error):
            continue
    # ECO-only imports are ambiguous, so prefer the name assigned at the
    # shortest move sequence; exact Chess.com opening URLs take precedence.
    generic={code:min(entries,key=lambda item:(item[0],len(item[1]),item[1]))[1]
             for code,entries in by_eco.items()}
    return generic,by_name


def chesscom_opening_label(opening="",eco_code="",eco_url=""):
    """Return a readable opening name with its ECO code when one is known."""
    source=str(opening or "").strip()
    code=str(eco_code or "").strip().upper()
    url=str(eco_url or "").strip()
    if source.startswith("http") and not url:
        url=source;source=""
    # Accept labels already saved by newer builds, while normalizing old ECO-only rows.
    existing=re.match(r"^(.*?)\s*[·|]\s*([A-E]\d{2})$",source,re.IGNORECASE)
    if existing:
        source=existing.group(1).strip();code=code or existing.group(2).upper()
    if CHESSCOM_ECO_PATTERN.fullmatch(source):
        code=code or source.upper();source=""
    if not CHESSCOM_ECO_PATTERN.fullmatch(code):code=""

    catalog,by_name=chesscom_opening_catalog()
    slug=""
    if url.startswith("http"):
        slug=urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    source_slug=source if source and " " not in source and "-" in source else ""
    lookup_key=_opening_name_key(slug or source_slug)
    if source_slug:source=""
    if source.casefold() in ("unknown","unknown opening","opening not found in local database","opening database not loaded"):
        source=""
    if not source and lookup_key:
        matches=by_name.get(lookup_key,[])
        exact=next((name for match_code,name in matches if code and match_code==code),None)
        if exact:source=exact
        elif matches:source=matches[0][1]
        else:source=re.sub(r"[-_]+"," ",slug or source_slug).title()
    if not source and code:source=catalog.get(code,"")
    if source and code:return f"{source} · {code}"
    return source or code or "Unknown opening"


def normalize_chesscom_username(value: str) -> str:
    username=(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,25}",username):
        raise ValueError("Enter a Chess.com username using 2–25 letters, numbers, underscores, or hyphens.")
    return username


def chesscom_game_identifier(game):
    if not isinstance(game,dict):return ""
    identifier=str(game.get("url","")).strip() or str(game.get("uuid","")).strip()
    if identifier:return identifier
    pgn=game.get("pgn")
    return hashlib.sha256(pgn.encode("utf-8")).hexdigest() if isinstance(pgn,str) and pgn else ""


def validate_chesscom_archive_url(value: str) -> str:
    parsed=urllib.parse.urlparse(str(value))
    try:port=parsed.port
    except ValueError:port=-1
    if (parsed.scheme!="https" or parsed.hostname!="api.chess.com" or port not in (None,443)
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not re.fullmatch(r"/pub/player/[A-Za-z0-9_-]+/games/\d{4}/(?:0[1-9]|1[0-2])/?",parsed.path)):
        raise ValueError("Chess.com supplied an invalid archive address.")
    return str(value)


def validate_chesscom_country_url(value: str) -> str:
    parsed=urllib.parse.urlparse(str(value))
    try:port=parsed.port
    except ValueError:port=-1
    if (parsed.scheme!="https" or parsed.hostname!="api.chess.com" or port not in (None,443)
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not re.fullmatch(r"/pub/country/[A-Za-z]{2}/?",parsed.path)):
        raise ValueError("Chess.com supplied an invalid country address.")
    return str(value)


def chesscom_request_json(url: str) -> dict:
    request=urllib.request.Request(url,headers={
        "User-Agent":"OfflineChess/1.0 (Chess.com public game archive importer)",
        "Accept":"application/json",
        "Accept-Encoding":"gzip",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=25) as response:
                payload=response.read()
                if response.headers.get("Content-Encoding")=="gzip":
                    import gzip
                    payload=gzip.decompress(payload)
            break
        except urllib.error.HTTPError as exc:
            if exc.code==429 and attempt<2:
                try:delay=max(1,min(10,int(exc.headers.get("Retry-After","2"))))
                except (TypeError,ValueError):delay=2
                time.sleep(delay);continue
            if exc.code==404:raise ValueError("Chess.com could not find that username or archive.") from exc
            if exc.code==429:raise ValueError("Chess.com is rate limiting requests. Wait a little, then try again.") from exc
            raise ValueError(f"Chess.com returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError,TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            transient = isinstance(reason, (ConnectionResetError, TimeoutError))
            if transient and attempt < 2:
                time.sleep(1)
                continue
            raise ValueError(f"Could not reach Chess.com: {reason}") from exc
    else:raise ValueError("Chess.com remained busy after retrying.")
    try:
        value=json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise ValueError("Chess.com returned data the app could not read.") from exc
    if not isinstance(value,dict):raise ValueError("Chess.com returned an unexpected response.")
    return value


class ChessComFetchWorker(QThread):
    archives_ready=pyqtSignal(object)
    month_ready=pyqtSignal(object)
    month_downloaded=pyqtSignal(str,object)
    progress=pyqtSignal(int,int)
    country_ready=pyqtSignal(str,str)
    countries_progress=pyqtSignal(int,int)
    countries_finished=pyqtSignal(int,int)
    failed=pyqtSignal(str)
    all_finished=pyqtSignal()

    def __init__(self,operation,username,archive_url="",opponents=None,parent=None,
                 known_game_ids=None,minimum_archive_month="",request_func=None):
        super().__init__(parent);self.operation=operation;self.cancel_event=threading.Event()
        self.username=normalize_chesscom_username(username);self.archive_url=archive_url;self.opponents=list(opponents or [])
        self.known_game_ids=set(known_game_ids or ());self.minimum_archive_month=str(minimum_archive_month or "")
        self.request_func=request_func or chesscom_request_json
        self.new_games_added=0;self.archives_checked=0

    def cancel(self):self.cancel_event.set()

    def run(self):
        try:
            if self.operation=="archives":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                data=self.request_func(url)
                archives=data.get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                self.archives_ready.emit(archives)
            elif self.operation=="month":
                data=self.request_func(validate_chesscom_archive_url(self.archive_url))
                games=data.get("games",[])
                if not isinstance(games,list):raise ValueError("Chess.com returned an invalid game list.")
                self.month_ready.emit(games)
            elif self.operation=="all":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                archives=self.request_func(url).get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                self.progress.emit(0,len(archives))
                for index,archive in enumerate(archives,1):
                    if self.cancel_event.is_set():break
                    data=self.request_func(validate_chesscom_archive_url(archive))
                    games=data.get("games",[])
                    if not isinstance(games,list):raise ValueError(f"Chess.com returned an invalid archive: {archive}")
                    self.month_downloaded.emit(str(archive),games)
                    self.progress.emit(index,len(archives))
                self.all_finished.emit()
            elif self.operation=="update":
                url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(self.username)}/games/archives"
                archives=self.request_func(url).get("archives",[])
                if not isinstance(archives,list):raise ValueError("Chess.com returned an invalid archive list.")
                relevant=[]
                for archive in archives:
                    match=re.search(r"/(\d{4})/(\d{2})/?$",str(archive))
                    if match and match.group(1)+"-"+match.group(2)>=self.minimum_archive_month:
                        relevant.append((match.group(1)+"-"+match.group(2),str(archive)))
                relevant.sort()
                self.progress.emit(0,len(relevant))
                seen=set(self.known_game_ids)
                for index,(_month,archive) in enumerate(relevant,1):
                    if self.cancel_event.is_set():break
                    data=self.request_func(validate_chesscom_archive_url(archive))
                    games=data.get("games",[])
                    if not isinstance(games,list):raise ValueError(f"Chess.com returned an invalid archive: {archive}")
                    new_games=[]
                    for game in games:
                        game_id=chesscom_game_identifier(game)
                        if game_id and game_id not in seen:
                            seen.add(game_id);new_games.append(game)
                    if new_games:
                        self.new_games_added+=len(new_games)
                        self.month_downloaded.emit(archive,new_games)
                    self.archives_checked=index
                    self.progress.emit(index,len(relevant))
                self.all_finished.emit()
            elif self.operation=="countries":
                country_names={};completed=0;total=len(self.opponents)
                for index,raw_opponent in enumerate(self.opponents,1):
                    if self.cancel_event.is_set():break
                    opponent=normalize_chesscom_username(raw_opponent)
                    profile_url=f"{CHESSCOM_API_ROOT}/player/{urllib.parse.quote(opponent)}"
                    try:
                        profile=self.request_func(profile_url)
                        country_url=validate_chesscom_country_url(profile.get("country",""))
                        if country_url not in country_names:
                            country=self.request_func(country_url)
                            country_names[country_url]=str(country.get("name") or country.get("code") or "")
                        country_name=country_names[country_url]
                    except ValueError as exc:
                        if "could not find that username" in str(exc).casefold():country_name=""
                        elif "invalid country address" in str(exc).casefold():country_name=""
                        else:raise
                    self.country_ready.emit(opponent,country_name)
                    completed=index;self.countries_progress.emit(completed,total)
                self.countries_finished.emit(completed,total)
            else:raise ValueError("Unknown Chess.com download operation.")
        except Exception as exc:self.failed.emit(str(exc))


class ChessComCastlingScanWorker(QThread):
    progress=pyqtSignal(int,int)
    scan_done=pyqtSignal(object)

    def __init__(self,records,parent=None):
        super().__init__(parent);self.records=list(records)

    def run(self):
        results=[];total=len(self.records)
        for index,record in enumerate(self.records,1):
            if self.isInterruptionRequested():break
            game_id=record.get("game_id");phase="Did not castle";castle_ply=None;error=""
            try:
                path=Path(record.get("pgn_path") or "")
                with path.open("r",encoding="utf-8-sig") as source:
                    game=chess.pgn.read_game(source)
                if game is None:raise ValueError("PGN contains no game")
                if game.errors:raise ValueError(f"PGN contains invalid moves: {game.errors[0]}")
                user_color=str(record.get("user_color") or "").strip().casefold()
                if user_color not in ("white","black"):user_color=None
                board=game.board()
                for ply,move in enumerate(game.mainline_moves(),1):
                    mover="white" if board.turn==chess.WHITE else "black"
                    if (user_color is None or mover==user_color) and board.is_castling(move):
                        phase=game_phase(board);castle_ply=ply;break
                    board.push(move)
            except (OSError,UnicodeError,ValueError,chess.InvalidMoveError,chess.IllegalMoveError) as exc:
                error=str(exc) or "Could not read PGN"
            results.append((game_id,phase,castle_ply,error))
            self.progress.emit(index,total)
        self.scan_done.emit(results)


class ChessComPgnStatsWorker(QThread):
    """Read saved PGNs once and derive small engine-free summaries in the background."""
    progress=pyqtSignal(int,int)
    scan_done=pyqtSignal(object)

    def __init__(self,records,parent=None):
        super().__init__(parent);self.records=list(records)

    def run(self):
        results=[];total=len(self.records)
        for index,record in enumerate(self.records,1):
            if self.isInterruptionRequested():break
            game_id=record.get("game_id");summary=None;error=""
            try:
                with Path(record.get("pgn_path") or "").open("r",encoding="utf-8-sig") as source:
                    game=chess.pgn.read_game(source)
                if game is None:raise ValueError("PGN contains no game")
                if game.errors:raise ValueError(f"PGN contains invalid moves: {game.errors[0]}")
                summary=extract_pgn_stats(game,record.get("time_control",""))
            except (OSError,UnicodeError,ValueError,chess.InvalidMoveError,chess.IllegalMoveError) as exc:
                error=str(exc) or "Could not read PGN"
                summary={"source":"pgn","version":PGN_STATS_VERSION,"error":error}
            results.append((game_id,summary))
            self.progress.emit(index,total)
        self.scan_done.emit(results)


class ProfileStore:
    """Small local summary database; full PGNs are optional and off by default."""
    def __init__(self):
        if os.name=="nt":
            base=Path(os.environ.get("LOCALAPPDATA",str(Path.home()/"AppData"/"Local")))
        else:
            base=Path(os.environ.get("XDG_DATA_HOME",str(Path.home()/".local"/"share")))
        self.path=base/"OfflineChess"/"profile.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=self.connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS games(
                game_key TEXT PRIMARY KEY, played_at TEXT NOT NULL, result TEXT NOT NULL,
                user_color TEXT NOT NULL, user_result REAL, opponent_elo INTEGER,
                elo_approx INTEGER NOT NULL, user_accuracy REAL,
                white_accuracy REAL, black_accuracy REAL,
                plies INTEGER NOT NULL, opening TEXT NOT NULL, average_loss REAL,
                pgn TEXT, stats_json TEXT NOT NULL DEFAULT '{}', review_json TEXT)""")
            columns={row[1] for row in db.execute("PRAGMA table_info(games)")}
            if "stats_json" not in columns:db.execute("ALTER TABLE games ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")
            if "review_json" not in columns:db.execute("ALTER TABLE games ADD COLUMN review_json TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('store_full_pgns','0')")
            db.execute("""CREATE TABLE IF NOT EXISTS chesscom_games(
                game_id TEXT PRIMARY KEY, username TEXT NOT NULL, played_at TEXT NOT NULL,
                result TEXT NOT NULL, user_color TEXT NOT NULL, user_result REAL,
                player_rating INTEGER, opponent TEXT NOT NULL, opponent_rating INTEGER,
                time_control TEXT NOT NULL, time_class TEXT NOT NULL, rated INTEGER NOT NULL,
                rules TEXT NOT NULL, opening TEXT NOT NULL, pgn_path TEXT NOT NULL,
                reviewed INTEGER NOT NULL DEFAULT 0, user_accuracy REAL, chesscom_accuracy REAL, predicted_elo INTEGER,
                review_json TEXT NOT NULL DEFAULT '[]', stats_json TEXT NOT NULL DEFAULT '{}',
                opponent_country TEXT NOT NULL DEFAULT '', opponent_country_checked INTEGER NOT NULL DEFAULT 0,
                castling_scanned INTEGER NOT NULL DEFAULT 0, castling_phase TEXT NOT NULL DEFAULT '', castling_ply INTEGER,
                castling_scan_version INTEGER NOT NULL DEFAULT 0,
                pgn_stats_version TEXT NOT NULL DEFAULT '', pgn_stats_json TEXT NOT NULL DEFAULT '{}')""")
            imported_columns={row[1] for row in db.execute("PRAGMA table_info(chesscom_games)")}
            if "stats_json" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'")
            if "opponent_country" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN opponent_country TEXT NOT NULL DEFAULT ''")
            if "opponent_country_checked" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN opponent_country_checked INTEGER NOT NULL DEFAULT 0")
            if "chesscom_accuracy" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN chesscom_accuracy REAL")
            if "castling_scanned" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_scanned INTEGER NOT NULL DEFAULT 0")
            if "castling_phase" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_phase TEXT NOT NULL DEFAULT ''")
            if "castling_ply" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_ply INTEGER")
            if "castling_scan_version" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN castling_scan_version INTEGER NOT NULL DEFAULT 0")
            if "pgn_stats_version" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN pgn_stats_version TEXT NOT NULL DEFAULT ''")
            if "pgn_stats_json" not in imported_columns:
                db.execute("ALTER TABLE chesscom_games ADD COLUMN pgn_stats_json TEXT NOT NULL DEFAULT '{}'")
            db.execute("CREATE INDEX IF NOT EXISTS chesscom_library_order ON chesscom_games(username COLLATE NOCASE,played_at DESC,game_id)")
            db.commit()
        finally:db.close()
        self._migrate_saved_accuracy_model()

    def connect(self):
        return sqlite3.connect(self.path,timeout=10)

    @staticmethod
    def _recalibrate_saved_accuracy(user_color,user_accuracy,white_accuracy,black_accuracy,
                                    stats_source,review_source):
        try:stats=json.loads(stats_source or "{}")
        except (TypeError,ValueError):stats={}
        try:review=json.loads(review_source) if review_source else None
        except (TypeError,ValueError):review=None
        moves=review.get("moves") if isinstance(review,dict) else None
        if isinstance(moves,list):
            by_side={"White":[],"Black":[]};by_phase={}
            valid_moves=0
            for move in moves:
                if not isinstance(move,dict):continue
                try:
                    raw=raw_move_accuracy(int(move.get("loss",0)),move.get("expected_loss"))
                    move["accuracy"]=calibrate_accuracy(raw);valid_moves+=1
                except (TypeError,ValueError):continue
                side=move.get("side")
                if side in by_side:by_side[side].append(raw)
                phase=move.get("phase")
                if side==user_color and phase in ("Opening","Middlegame","Endgame"):
                    by_phase.setdefault(phase,[]).append(raw)
            if valid_moves:
                aggregate=lambda values:calibrate_accuracy(sum(values)/len(values)) if values else None
                white_accuracy=aggregate(by_side["White"]) if by_side["White"] else white_accuracy
                black_accuracy=aggregate(by_side["Black"]) if by_side["Black"] else black_accuracy
                recalibrated_user_accuracy=aggregate(by_side.get(user_color,[]))
                if recalibrated_user_accuracy is not None:user_accuracy=recalibrated_user_accuracy
                if isinstance(stats,dict):
                    stats["phase_accuracy"]={phase:aggregate(values) for phase,values in by_phase.items()}
                return (user_accuracy,white_accuracy,black_accuracy,
                        json.dumps(stats,separators=(",",":")),
                        json.dumps(review,separators=(",",":")))
        def recalibrate(value):
            try:return calibrate_accuracy(float(value)) if value is not None else None
            except (TypeError,ValueError):return value
        if isinstance(stats,dict) and isinstance(stats.get("phase_accuracy"),dict):
            stats["phase_accuracy"]={phase:recalibrate(value)
                                     for phase,value in stats["phase_accuracy"].items()}
        return (recalibrate(user_accuracy),recalibrate(white_accuracy),recalibrate(black_accuracy),
                json.dumps(stats,separators=(",",":")) if isinstance(stats,dict) else stats_source,
                review_source)

    def _migrate_saved_accuracy_model(self):
        db=self.connect()
        try:
            saved=db.execute("SELECT value FROM settings WHERE key='accuracy_model_version'").fetchone()
            if saved and saved[0]==ACCURACY_MODEL_VERSION:return
            local_rows=db.execute("SELECT game_key,user_color,user_accuracy,white_accuracy,black_accuracy,stats_json,review_json FROM games").fetchall()
            local_updates=[]
            for key,user_color,user_accuracy,white_accuracy,black_accuracy,stats_source,review_source in local_rows:
                values=self._recalibrate_saved_accuracy(user_color,user_accuracy,white_accuracy,black_accuracy,
                                                         stats_source,review_source)
                local_updates.append((*values,key))
            db.executemany("UPDATE games SET user_accuracy=?,white_accuracy=?,black_accuracy=?,stats_json=?,review_json=? WHERE game_key=?",local_updates)
            chesscom_rows=db.execute("SELECT game_id,user_color,user_accuracy,stats_json,review_json FROM chesscom_games").fetchall()
            chesscom_updates=[]
            for game_id,user_color,user_accuracy,stats_source,review_source in chesscom_rows:
                user_accuracy,_,_,stats_source,review_source=self._recalibrate_saved_accuracy(
                    user_color,user_accuracy,None,None,stats_source,review_source)
                chesscom_updates.append((user_accuracy,stats_source,review_source,game_id))
            db.executemany("UPDATE chesscom_games SET user_accuracy=?,stats_json=?,review_json=? WHERE game_id=?",chesscom_updates)
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('accuracy_model_version',?)",
                       (ACCURACY_MODEL_VERSION,))
            db.commit()
        finally:db.close()

    def chesscom_root(self,username):
        safe=normalize_chesscom_username(username)
        return self.path.parent/"chesscom_games"/safe.lower()

    def save_chesscom_game(self,username,game):
        username=normalize_chesscom_username(username)
        if not isinstance(game,dict) or not isinstance(game.get("pgn"),str) or not game["pgn"].strip():
            raise ValueError("The selected archive entry has no PGN to save.")
        white=game.get("white") if isinstance(game.get("white"),dict) else {}
        black=game.get("black") if isinstance(game.get("black"),dict) else {}
        if str(white.get("username","")).casefold()==username.casefold():user_color="White";player=white;opponent_data=black
        elif str(black.get("username","")).casefold()==username.casefold():user_color="Black";player=black;opponent_data=white
        else:raise ValueError("This game does not list the selected account as a player.")
        game_id=chesscom_game_identifier(game)
        if not game_id:raise ValueError("The selected archive entry has no game ID.")
        stable_id=hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:16]
        end_time=game.get("end_time")
        try:played_at=datetime.fromtimestamp(int(end_time),datetime.now().astimezone().tzinfo).isoformat(timespec="minutes")
        except (TypeError,ValueError,OverflowError,OSError):
            try:played_at=chess.pgn.read_game(io.StringIO(game["pgn"])).headers.get("Date","????.??.??").replace(".","-")
            except Exception:played_at="Unknown date"
        white_result=game.get("white",{}).get("result","") if isinstance(game.get("white"),dict) else ""
        black_result=game.get("black",{}).get("result","") if isinstance(game.get("black"),dict) else ""
        pgn_game=chess.pgn.read_game(io.StringIO(game["pgn"]))
        if pgn_game is None:raise ValueError("The downloaded PGN could not be parsed.")
        if pgn_game.errors:raise ValueError(f"The downloaded PGN contains invalid moves: {pgn_game.errors[0]}")
        board=pgn_game.board()
        for move in pgn_game.mainline_moves():
            if move not in board.legal_moves:raise ValueError("The downloaded PGN contains an illegal move.")
            board.push(move)
        result=pgn_game.headers.get("Result",game.get("result","*"))
        user_result=(1.0 if result==("1-0" if user_color=="White" else "0-1") else
                     .5 if result=="1/2-1/2" else 0.0 if result in ("1-0","0-1") else None)
        root=self.chesscom_root(username)
        try:month=datetime.fromisoformat(played_at).strftime("%Y-%m")
        except ValueError:month="unknown-date"
        folder=root/month;folder.mkdir(parents=True,exist_ok=True)
        opponent=str(opponent_data.get("username","Unknown"))
        filename=f"{played_at[:10]}_{re.sub(r'[^A-Za-z0-9_-]+','_',opponent)[:24]}_{stable_id}.pgn"
        target=folder/filename;temporary=target.with_suffix(".pgn.tmp")
        def rating(person):
            try:return int(person.get("rating"))
            except (TypeError,ValueError):return None
        accuracies=game.get("accuracies") if isinstance(game.get("accuracies"),dict) else {}
        try:
            chesscom_accuracy=float(accuracies.get(user_color.lower()))
            if not math.isfinite(chesscom_accuracy) or not 0<=chesscom_accuracy<=100:chesscom_accuracy=None
        except (TypeError,ValueError):chesscom_accuracy=None
        opening_url=game.get("eco") or pgn_game.headers.get("ECOUrl","")
        opening=chesscom_opening_label(pgn_game.headers.get("Opening",""),
                                       pgn_game.headers.get("ECO",""),opening_url)
        country=str(opponent_data.get("country","")).strip()
        if country.startswith("http"):country=""
        record=(game_id,username,played_at,result,user_color,user_result,rating(player),opponent,rating(opponent_data),
                str(game.get("time_control","")),str(game.get("time_class","")),int(bool(game.get("rated",False))),
                str(game.get("rules","chess")),str(opening),str(target),0,None,chesscom_accuracy,None,"[]",country,int(bool(country)))
        previous_pgn=target.read_bytes() if target.exists() else None
        try:
            temporary.write_text(game["pgn"].rstrip()+"\n",encoding="utf-8")
            temporary.replace(target)
            db=self.connect()
            try:
                db.execute("""INSERT INTO chesscom_games(game_id,username,played_at,result,user_color,user_result,
                    player_rating,opponent,opponent_rating,time_control,time_class,rated,rules,opening,pgn_path,
                    reviewed,user_accuracy,chesscom_accuracy,predicted_elo,review_json,opponent_country,opponent_country_checked)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(game_id) DO UPDATE SET username=excluded.username,played_at=excluded.played_at,
                    result=excluded.result,user_color=excluded.user_color,user_result=excluded.user_result,
                    player_rating=excluded.player_rating,opponent=excluded.opponent,opponent_rating=excluded.opponent_rating,
                    time_control=excluded.time_control,time_class=excluded.time_class,rated=excluded.rated,
                    rules=excluded.rules,opening=excluded.opening,pgn_path=excluded.pgn_path,
                    chesscom_accuracy=COALESCE(excluded.chesscom_accuracy,chesscom_games.chesscom_accuracy)""",record)
                db.commit()
            finally:db.close()
        except Exception:
            try:
                if previous_pgn is None:
                    target.unlink(missing_ok=True)
                else:
                    restore=target.with_suffix(".pgn.restore")
                    restore.write_bytes(previous_pgn)
                    restore.replace(target)
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return game_id

    def chesscom_games(self,username):
        username=normalize_chesscom_username(username);db=self.connect()
        try:
            rows=db.execute("""SELECT game_id,username,played_at,result,user_color,user_result,player_rating,opponent,
                opponent_rating,time_control,time_class,rated,rules,opening,pgn_path,reviewed,user_accuracy,
                predicted_elo,review_json,stats_json,opponent_country,opponent_country_checked,chesscom_accuracy,
                castling_scanned,castling_phase,castling_ply,castling_scan_version,pgn_stats_version,pgn_stats_json
                FROM chesscom_games WHERE username=? COLLATE NOCASE ORDER BY played_at DESC,game_id""",
                (username,)).fetchall()
        finally:db.close()
        columns=("game_id","username","played_at","result","user_color","user_result","player_rating","opponent",
                 "opponent_rating","time_control","time_class","rated","rules","opening","pgn_path","reviewed",
                 "user_accuracy","predicted_elo","review_json","stats_json","opponent_country","opponent_country_checked",
                 "chesscom_accuracy","castling_scanned","castling_phase","castling_ply","castling_scan_version",
                 "pgn_stats_version","pgn_stats_json")
        records=[dict(zip(columns,row)) for row in rows]
        for record in records:
            record["opening"]=chesscom_opening_label(record.get("opening",""))
        return records

    def chesscom_usernames(self):
        """Return usernames that already have downloaded games in this profile."""
        db=self.connect()
        try:
            rows=db.execute("SELECT DISTINCT username FROM chesscom_games ORDER BY username COLLATE NOCASE").fetchall()
        finally:db.close()
        return [str(row[0]) for row in rows if row and row[0]]

    def set_chesscom_opponent_country(self,username,opponent,country):
        username=normalize_chesscom_username(username);opponent=normalize_chesscom_username(opponent)
        db=self.connect()
        try:
            cursor=db.execute("""UPDATE chesscom_games SET opponent_country=?,opponent_country_checked=1
                WHERE username=? COLLATE NOCASE AND opponent=? COLLATE NOCASE""",
                (str(country or "").strip(),username,opponent))
            db.commit();return cursor.rowcount
        finally:db.close()

    def remove_chesscom_game(self,game_id):
        db=self.connect()
        try:
            row=db.execute("SELECT pgn_path FROM chesscom_games WHERE game_id=?",(game_id,)).fetchone()
            if row:
                db.execute("DELETE FROM chesscom_games WHERE game_id=?",(game_id,))
                db.commit()
        finally:db.close()
        if row:
            try:Path(row[0]).unlink(missing_ok=True)
            except OSError:pass

    def set_chesscom_review(self,game_id,reviewed,user_accuracy=None,predicted_elo=None,review_json="[]",stats_json="{}"):
        db=self.connect()
        try:
            db.execute("UPDATE chesscom_games SET reviewed=?,user_accuracy=?,predicted_elo=?,review_json=?,stats_json=? WHERE game_id=?",
                       (int(bool(reviewed)),user_accuracy,predicted_elo,review_json,stats_json,game_id));db.commit()
        finally:db.close()

    def set_chesscom_castling_scans(self,scans):
        rows=[]
        for game_id,phase,ply in scans:
            if phase not in ("Opening","Middlegame","Endgame","Did not castle"):
                raise ValueError("Invalid castling phase")
            rows.append((phase,ply,CASTLING_SCAN_VERSION,game_id))
        if not rows:return
        db=self.connect()
        try:
            db.executemany("UPDATE chesscom_games SET castling_scanned=1,castling_phase=?,castling_ply=?,castling_scan_version=? WHERE game_id=?",rows);db.commit()
        finally:db.close()

    def set_chesscom_pgn_stats(self,results):
        rows=[]
        for game_id,summary in results:
            if not isinstance(summary,dict) or summary.get("source")!="pgn" or summary.get("version")!=PGN_STATS_VERSION:
                raise ValueError("Invalid PGN statistics summary")
            rows.append((PGN_STATS_VERSION,json.dumps(summary,separators=(",",":")),game_id))
        if not rows:return
        db=self.connect()
        try:
            db.executemany("UPDATE chesscom_games SET pgn_stats_version=?,pgn_stats_json=? WHERE game_id=?",rows)
            db.commit()
        finally:db.close()

    def full_pgns_enabled(self):
        db=self.connect()
        try:
            row=db.execute("SELECT value FROM settings WHERE key='store_full_pgns'").fetchone()
            return bool(row and row[0]=="1")
        finally:db.close()

    def get_setting(self,key,default=None):
        db=self.connect()
        try:
            row=db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            return row[0] if row else default
        finally:db.close()

    def set_setting(self,key,value):
        db=self.connect()
        try:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
            db.commit()
        finally:db.close()

    def set_full_pgns(self,enabled):
        db=self.connect()
        try:
            db.execute("UPDATE settings SET value=? WHERE key='store_full_pgns'",("1" if enabled else "0",))
            db.commit()
        finally:db.close()

    def save_review(self,record):
        columns=("game_key","played_at","result","user_color","user_result","opponent_elo","elo_approx",
                 "user_accuracy","white_accuracy","black_accuracy","plies","opening","average_loss","pgn","stats_json","review_json")
        updates=[f"{c}=excluded.{c}" for c in columns[1:] if c!="pgn"]
        updates.append("pgn=COALESCE(excluded.pgn,games.pgn)")
        db=self.connect()
        try:
            db.execute(f"INSERT INTO games({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                       "ON CONFLICT(game_key) DO UPDATE SET " + ",".join(updates),
                       tuple(record[c] for c in columns))
            db.commit()
        finally:db.close()

    def games(self):
        db=self.connect()
        try:
            rows=db.execute("SELECT game_key,played_at,result,user_color,user_result,opponent_elo,elo_approx,user_accuracy,white_accuracy,black_accuracy,plies,opening,average_loss,pgn,stats_json,review_json FROM games ORDER BY played_at DESC").fetchall()
        finally:db.close()
        names=("game_key","played_at","result","user_color","user_result","opponent_elo","elo_approx","user_accuracy","white_accuracy","black_accuracy","plies","opening","average_loss","pgn","stats_json","review_json")
        return [dict(zip(names,row)) for row in rows]

    def clear_pgns(self):
        db=self.connect()
        try:
            imported=[row[0] for row in db.execute("SELECT pgn_path FROM chesscom_games WHERE pgn_path!=''")]
            db.execute("UPDATE games SET pgn=NULL,review_json=NULL")
            db.execute("UPDATE chesscom_games SET pgn_path=''");db.commit()
        finally:db.close()
        for path in imported:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass

    def delete_game(self,game_key):
        db=self.connect()
        try:db.execute("DELETE FROM games WHERE game_key=?",(game_key,));db.commit()
        finally:db.close()

    def clear_all(self):
        db=self.connect()
        try:
            imported=[row[0] for row in db.execute("SELECT pgn_path FROM chesscom_games")]
            db.execute("DELETE FROM games");db.execute("DELETE FROM chesscom_games")
            for table in ("training_cards", "training_attempts"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    db.execute("DELETE FROM " + table)
            db.execute("DELETE FROM settings WHERE key='puzzle_challenge_high_score'");db.commit()
        finally:db.close()
        for path in imported:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass


def prepare_chesscom_pgn(record):
    """Preserve the source PGN while adding the player's side for local review coaching."""
    pgn_text=Path(record["pgn_path"]).read_text(encoding="utf-8-sig")
    game=chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:raise ValueError("The saved Chess.com PGN could not be read.")
    if game.errors:raise ValueError(f"The saved Chess.com PGN contains invalid moves: {game.errors[0]}")
    game.headers["OfflineChessUserColor"]=record["user_color"]
    game.headers["OfflineChessPlayedLocally"]="0"
    try:game.headers["Date"]=datetime.fromisoformat(record["played_at"]).strftime("%Y.%m.%d")
    except (ValueError,TypeError):pass
    return game.accept(chess.pgn.StringExporter(headers=True,variations=True,comments=True))+"\n"


def chesscom_pgn_clock_positions(game):
    """Return the recorded White and Black clocks at each mainline position."""
    positions=[(None,None)];white_clock=black_clock=None;node=game
    for _move in game.mainline_moves():
        node=node.variation(0)
        clock=node.clock()
        if clock is not None:
            if node.parent.board().turn==chess.WHITE:white_clock=clock
            else:black_clock=clock
        positions.append((white_clock,black_clock))
    return positions


def pgn_playback_delays(game, fallback_seconds=1.0):
    """Estimate each mainline move's elapsed time from its recorded clock tags."""
    moves=list(game.mainline_moves())
    if not moves:return []
    try:fallback=max(0.0,float(fallback_seconds))
    except (TypeError,ValueError):fallback=1.0
    delays=[fallback]*len(moves)
    nodes=[];node=game
    for _move in moves:
        node=node.variation(0);nodes.append(node)
    if not any(node.clock() is not None for node in nodes):return delays

    control=re.fullmatch(r"(\d+(?:\.\d+)?)(?:\+(\d+(?:\.\d+)?))?",
                         str(game.headers.get("TimeControl","")).strip())
    try:
        initial=float(control.group(1)) if control else None
        increment=float(control.group(2) or 0) if control else 0.0
    except (TypeError,ValueError):initial=None;increment=0.0
    clocks={chess.WHITE:initial,chess.BLACK:initial}
    pending={chess.WHITE:[],chess.BLACK:[]}
    for index,child in enumerate(nodes):
        side=child.parent.board().turn
        pending[side].append(index)
        clock=child.clock()
        if clock is None:continue
        try:clock=float(clock)
        except (TypeError,ValueError):continue
        if not math.isfinite(clock):continue
        previous=clocks[side]
        if previous is not None:
            elapsed=max(0.0,previous+increment*len(pending[side])-clock)
            per_move=elapsed/len(pending[side])
            for move_index in pending[side]:delays[move_index]=per_move
        clocks[side]=clock
        pending[side].clear()
    return delays


def format_chesscom_clock(seconds):
    if seconds is None:return "—:——"
    try:seconds=max(0.0,float(seconds))
    except (TypeError,ValueError):return "—:——"
    whole=int(seconds);fraction=seconds-whole
    if whole>=3600:
        hours,remaining=divmod(whole,3600);minutes,secs=divmod(remaining,60)
        return f"{hours}:{minutes:02d}:{secs:02d}{f'.{int(fraction*10)}' if fraction>=.05 else ''}"
    minutes,secs=divmod(whole,60)
    return f"{minutes}:{secs:02d}{f'.{int(fraction*10)}' if fraction>=.05 else ''}"

def predicted_performance_elo(games,min_games=8):
    """Estimate result-based playing performance; never present it as an official rating."""
    usable=[]
    for game in games:
        try:
            if not bool(game.get("rated",False)):continue
            rating=int(game["opponent_rating"]);score=float(game["user_result"])
            if 100<=rating<=4000 and 0<=score<=1:usable.append((rating,score))
        except (KeyError,TypeError,ValueError):continue
    if len(usable)<min_games:return None
    mean_opponent=sum(rating for rating,_ in usable)/len(usable)
    target=sum(score for _,score in usable)+.5
    def total_expected(player_rating):
        return sum(1/(1+10**max(-10,min(10,(opponent-player_rating)/400))) for opponent,_ in usable)+1/(1+10**max(-10,min(10,(mean_opponent-player_rating)/400)))
    low,high=100.0,4000.0
    for _ in range(64):
        middle=(low+high)/2
        if total_expected(middle)<target:low=middle
        else:high=middle
    return round((low+high)/2)

__all__ = [
    'PACKAGE_ROOT',
    'CHESSCOM_API_ROOT',
    'CHESSCOM_ECO_PATTERN',
    'CASTLING_SCAN_VERSION',
    'chesscom_opening_catalog',
    'chesscom_opening_label',
    'normalize_chesscom_username',
    'chesscom_game_identifier',
    'validate_chesscom_archive_url',
    'validate_chesscom_country_url',
    'chesscom_request_json',
    'ChessComFetchWorker',
    'ChessComCastlingScanWorker',
    'ChessComPgnStatsWorker',
    'ProfileStore',
    'prepare_chesscom_pgn',
    'chesscom_pgn_clock_positions',
    'pgn_playback_delays',
    'format_chesscom_clock',
    'predicted_performance_elo',
]
