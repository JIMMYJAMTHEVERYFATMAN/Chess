from contextlib import closing
"""Benchmark rich reviewed libraries in a disposable SQLite database."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from offline_chess.chesscom_data import ProfileStore
from offline_chess.game_library import GameLibrary
from unittest.mock import patch
import os


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games',type=int,default=10000)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'LOCALAPPDATA':folder,'XDG_DATA_HOME':folder}):
        store=ProfileStore()
        # Synthetic 100-ply reviews with realistic repeated per-move annotations.
        review=json.dumps({'version':14,'moves':[{'ply':i,'san':'e4','theme':'Fork','explanation':'Compare the forcing continuation. '*5} for i in range(100)]})
        with closing(store.connect()) as db, db:
            db.executemany('''INSERT INTO chesscom_games(game_id,username,played_at,result,user_color,
                opponent,time_control,time_class,rated,rules,opening,pgn_path,reviewed,review_json)
                VALUES(?, 'benchmark', '2026-01-01', '1-0','White','Opponent','600','rapid',1,'chess','Bird Opening','',1,?)''',
                ((str(i),review) for i in range(args.games)))
        start=time.perf_counter(); full=store.chesscom_games('benchmark');full_ms=(time.perf_counter()-start)*1000
        full_bytes=sum(len(json.dumps(row)) for row in full)
        del full
        start=time.perf_counter();page,total,_=GameLibrary(store).page('benchmark',search='Opponent',reviewed=True);page_ms=(time.perf_counter()-start)*1000
        result={'games':total,'review_bytes_per_game':len(review),'full_query_ms':round(full_ms,2),
                'page_query_ms':round(page_ms,2),'full_result_bytes':full_bytes,
                'page_result_bytes':sum(len(json.dumps(row)) for row in page),'page_rows':len(page),
                'scope':'Local SQLite queries; excludes Qt rendering and whole-profile insight aggregation'}
        output=json.dumps(result,indent=2)+'\n'
        if args.output:args.output.write_text(output)
        print(output)


if __name__=='__main__':main()
