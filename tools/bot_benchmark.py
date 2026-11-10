"""Resumable local bot evaluation. Run with python -m tools.bot_benchmark --help.

Match results measure strength relative to a specified Stockfish configuration,
not a validated Chess.com Elo. JSONL contains positions and should remain local
when evaluating personal PGNs. Each finished game/position is flushed to disk.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random

import chess
import chess.engine
import chess.pgn
from offline_chess.bot_policy import choose_candidate


def append(path, row):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')
        f.flush()
        __import__('os').fsync(f.fileno())


def interval(scores):
    """Conservative bounded-score Hoeffding 95% interval (draws included)."""
    if not scores:
        return None
    mean = sum(scores)/len(scores)
    radius = math.sqrt(math.log(40)/(2*len(scores)))
    return [max(0, mean-radius), min(1, mean+radius)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['matches', 'positions'])
    parser.add_argument('--engine', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pgn', type=Path)
    parser.add_argument('--elo', type=int, default=800)
    parser.add_argument('--reference-elo', type=int, default=1320)
    parser.add_argument('--style', choices=['balanced','attacking','solid'], default='balanced')
    parser.add_argument('--games', type=int, default=20)
    parser.add_argument('--nodes', type=int, default=1000)
    parser.add_argument('--max-plies', type=int, default=400)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.nodes < 1 or args.games < 1 or args.max_plies < 2:
        parser.error('nodes/games must be positive and max-plies at least 2')
    if args.mode == 'positions' and not args.pgn:
        parser.error('positions requires --pgn')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    meta = {k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items() if k not in ('output','games')}
    meta['engine_sha256'] = hashlib.sha256(Path(args.engine).read_bytes()).hexdigest()
    meta['pgn_sha256'] = hashlib.sha256(args.pgn.read_bytes()).hexdigest() if args.pgn else None
    rows = [json.loads(line) for line in args.output.read_text().splitlines()] if args.output.exists() else []
    if rows and rows[0].get('configuration') != meta:
        parser.error('Output belongs to a different configuration; choose a new output path')
    done = {row['id'] for row in rows[1:]}
    with chess.engine.SimpleEngine.popen_uci(args.engine) as engine:
        if not rows:
            append(args.output, {'configuration':meta,'engine':engine.id})
        engine.configure({'Threads':1, 'Hash':16})
        limit = chess.engine.Limit(nodes=args.nodes)
        def candidates(board):
            engine.configure({'UCI_LimitStrength':True, 'UCI_Elo':max(1320,args.elo),
                              'Skill Level':0 if args.elo<1320 else round((args.elo-1320)/(3190-1320)*20)})
            return engine.analyse(board, limit, multipv=min(48,board.legal_moves.count()), game=object())
        if args.mode == 'positions':
            with args.pgn.open(encoding='utf-8-sig') as f:
                game_no = 0
                while (game := chess.pgn.read_game(f)) is not None:
                    if game.errors:
                        raise ValueError(f'Malformed PGN game {game_no}')
                    board = game.board()
                    for ply, move in enumerate(game.mainline_moves()):
                        key = f'{game_no}:{ply}'
                        if key not in done:
                            infos = candidates(board)
                            scored = [(info['pv'][0],info['score'].pov(board.turn).score(mate_score=100000)) for info in infos]
                            # Estimate sampling probabilities without consuming the match RNG.
                            rng = random.Random(args.seed + game_no*10000 + ply)
                            counts = {}
                            for _ in range(2000):
                                picked = choose_candidate(board, infos, args.elo, args.style, rng)
                                counts[picked.uci()] = counts.get(picked.uci(),0)+1
                            observed = counts.get(move.uci(),0)/2000
                            best = max(value for _,value in scored)
                            losses = {m.uci():max(0,best-value) for m,value in scored}
                            append(args.output, {'id':key,'game':game_no,'fold':game_no%5,
                                'player_elo':game.headers.get('WhiteElo' if board.turn else 'BlackElo'),
                                'fen':board.fen(),'played':move.uci(),'covered':move in dict(scored),
                                'nll':-math.log(max(observed,1e-9)), 'top_frequency':counts.get(scored[0][0].uci(),0)/2000,
                                'expected_capped_loss':sum(counts.get(m,0)*min(1500,loss) for m,loss in losses.items())/2000,
                                'loss_rates':{str(t):sum(counts.get(m,0) for m,l in losses.items() if l>=t)/2000 for t in (50,100,300,500)},
                                'candidates':[(m.uci(),v) for m,v in scored]})
                        board.push(move)
                    game_no += 1
        else:
            for game_no in range(args.games):
                key = str(game_no)
                if key in done:
                    continue
                board = chess.Board()
                bot_color = game_no%2 == 0
                rng = random.Random(args.seed + game_no)
                moves = []
                while not board.is_game_over(claim_draw=True) and len(moves)<args.max_plies:
                    if board.turn == bot_color:
                        if args.elo >= 1320 and args.style == 'balanced':
                            engine.configure({'UCI_LimitStrength':True,'UCI_Elo':args.elo,
                                              'Skill Level':round((args.elo-1320)/(3190-1320)*20)})
                            move = engine.play(board,limit,game=object()).move
                        else:
                            move = choose_candidate(board, candidates(board), args.elo, args.style, rng)
                    else:
                        engine.configure({'UCI_LimitStrength':True,'UCI_Elo':args.reference_elo,'Skill Level':20})
                        move = engine.play(board,limit,game=object()).move
                    board.push(move)
                    moves.append(move.uci())
                outcome = board.outcome(claim_draw=True)
                score = (.5 if outcome.winner is None else float(outcome.winner==bot_color)) if outcome else None
                append(args.output, {'id':key,'bot_color':'White' if bot_color else 'Black',
                    'score':score,'result':outcome.result() if outcome else '*','truncated':outcome is None,'moves':moves})
    rows = [json.loads(line) for line in args.output.read_text().splitlines()][1:]
    if args.mode == 'matches':
        scores = [row['score'] for row in rows if row['score'] is not None]
        print(json.dumps({'completed':len(scores),'truncated':len(rows)-len(scores),
                          'score':sum(scores)/len(scores) if scores else None,'score_95_interval':interval(scores)}))
    else:
        print(json.dumps({'positions':len(rows),'coverage':sum(r['covered'] for r in rows)/len(rows) if rows else None,
            'game_folds':{str(f):{'positions':len(group),'mean_nll':sum(r['nll'] for r in group)/len(group)}
                for f in range(5) if (group := [r for r in rows if r['fold']==f])}}))


if __name__ == '__main__':
    main()
