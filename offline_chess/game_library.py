"""Bounded database queries for the downloaded-game library.

Insight aggregation remains separate; listing games never transfers review JSON
or PGN stats. Full records are loaded only when opening a selected game.
"""
import sqlite3
from .chesscom_data import normalize_chesscom_username, chesscom_opening_label

SUMMARY_COLUMNS = ('game_id','username','played_at','result','user_color','user_result',
    'player_rating','opponent','opponent_rating','time_control','time_class','rated','rules',
    'opening','pgn_path','reviewed','user_accuracy','chesscom_accuracy','predicted_elo')


class GameLibrary:
    def __init__(self, profile):
        self.profile = profile

    def page(self, username, *, search='', speed='', reviewed=None, page=0, size=100, allowed_ids=None):
        username = normalize_chesscom_username(username)
        size = max(1,min(100,int(size)))
        where = ['username=? COLLATE NOCASE']
        params = [username]
        if search.strip():
            where.append('(instr(casefold(opponent),?)>0 OR instr(opening_search(opening),?)>0)')
            params.extend([search.strip().casefold()]*2)
        if speed:
            where.append('time_class=? COLLATE NOCASE')
            params.append(speed)
        if reviewed is not None:
            where.append('reviewed=?')
            params.append(int(reviewed))
        with self.profile.connect() as db:
            db.row_factory = sqlite3.Row
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            db.create_function('opening_search',1,lambda value:chesscom_opening_label(value).casefold(),deterministic=True)
            if allowed_ids is not None:
                db.execute('CREATE TEMP TABLE library_ids(game_id TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO library_ids VALUES(?)', ((str(key),) for key in allowed_ids))
                where.append('game_id IN (SELECT game_id FROM library_ids)')
            clause = ' AND '.join(where)
            total = db.execute('SELECT COUNT(*) FROM chesscom_games WHERE '+clause,params).fetchone()[0]
            page = max(0,min(int(page),max(0,(total-1)//size)))
            rows = db.execute('SELECT '+','.join(SUMMARY_COLUMNS)+' FROM chesscom_games WHERE '+clause+
                ' ORDER BY played_at DESC,game_id LIMIT ? OFFSET ?',params+[size,page*size]).fetchall()
        records = [dict(row) for row in rows]
        for record in records:
            record['opening'] = chesscom_opening_label(record['opening'])
        return records,total,page

    def get(self, game_id):
        with self.profile.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM chesscom_games WHERE game_id=?',(game_id,)).fetchone()
        return dict(row) if row else None
