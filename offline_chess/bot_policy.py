"""Bot sampling and conservative adaptive strength, independent of Qt."""
import math
import random
from .config import MIN_ELO, MIN_TARGET_ELO, MAX_ELO

STYLES = ("balanced", "attacking", "solid")


def adaptive_target(current, player_score):
    """Adjust only between completed games, never more than 50 points."""
    if player_score not in (0.0, 0.5, 1.0):
        raise ValueError("A completed game score is required")
    return max(MIN_TARGET_ELO, min(MAX_ELO, current + round(100 * (player_score - .5))))


def choose_candidate(board,candidates,elo,style="balanced",rng=None):
    """Add target-sensitive, evaluation-aware mistakes to Stockfish's candidate choices."""
    softness=max(0.0,min(1.0,(MIN_ELO-int(elo))/(MIN_ELO-MIN_TARGET_ELO)))
    scored_moves=[]
    for candidate in candidates:
        pv=candidate.get("pv",[]);score=candidate.get("score")
        if not pv or pv[0] not in board.legal_moves or score is None:continue
        if any(existing==pv[0] for existing,_value in scored_moves):continue
        value=score.pov(board.turn).score(mate_score=100_000)
        if value is not None:scored_moves.append((pv[0],value))
    if not scored_moves:return None
    scored_moves.sort(key=lambda item:item[1],reverse=True)
    best_score=scored_moves[0][1]
    base_temperature=300+200*softness**1.4
    calibration_blend=softness
    check_scale=.55 if board.is_check() else 1.0
    legacy_rank_power=1.2-.4*softness
    # Mix normalized distributions so the rating curve stays smooth near both ends.
    legacy_weights=[];human_weights=[]
    for index,(_candidate,value) in enumerate(scored_moves):
        rank=index+1;gap=min(100_000,best_score-value)
        legacy_weights.append(rank**(-legacy_rank_power)*math.exp(-gap/(base_temperature*check_scale)))
        human_weights.append(rank**(-.2)*math.exp(-gap/(10_000_000*check_scale)))
    legacy_total=sum(legacy_weights);human_total=sum(human_weights)
    weights=[(1-calibration_blend)*legacy/legacy_total+calibration_blend*human/human_total
             for legacy,human in zip(legacy_weights,human_weights)]
    if style != "balanced":
        for i, (move, value) in enumerate(scored_moves):
            after = board.copy(stack=False)
            after.push(move)
            if style == "attacking":
                weights[i] *= 1.6 if after.is_check() or board.is_capture(move) else 1.0
            elif style == "solid":
                weights[i] *= math.exp(-min(1500, max(0, best_score-value))/350)
    return (rng or random).choices([candidate for candidate,_value in scored_moves],weights=weights,k=1)[0]

