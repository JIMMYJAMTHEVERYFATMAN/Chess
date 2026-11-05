# Sub-1320 Elo Bot Calibration Handoff

## Objective and status

Make Stockfish bots below 1320 Elo play closer to their displayed rating and to human opponents from Chess.com. The work is **incomplete** and the goal is currently **paused** at the user's request. The current move-selection policy is implemented, but its actual playing Elo has not been demonstrated. Do not mark this goal complete until the remaining validation below is done.

## Current implementation

In `offline_chess/engine_worker.py`:

- Stockfish's calibrated `UCI_Elo` floor is 1320. For a sub-1320 target, the worker configures UCI Elo to that floor and Skill Level 0, then models the lower strength by sampling from the engine's candidate moves.
- For sub-1320 bot turns, `play_bot` asks for up to 48 candidate moves and searches for roughly 0.35–0.47 seconds, with a small random duration and legal-move-count adjustment.
- `_choose_sub_1320_candidate` ranks candidates from the side-to-move perspective, then blends a temperature/rank-based legacy distribution with a broad human-move distribution. The blend increases linearly from zero at 1320 Elo to one at 400 Elo.
- Candidate selection is stochastic through `random.choices`.

The unit tests verify the formula, smooth rating trend, and Black-to-move ordering. They do not establish that the bot's match results correspond to its nominal Elo.

## Evidence available

### Local PGN coverage

`test pgns/chess_com_games_2026-11-01.pgn` contains 50 Chess.com archive games. A current count of moves by the player whose PGN Elo is in each band is:

| Rating band | Games containing a player in band | Moves by players in band |
|---|---:|---:|
| 100–199 | 2 | 33 |
| 200–299 | 10 | 207 |
| 300–399 | 44 | 1,734 |
| 400–499 | 21 | 692 |
| 500–599 | 2 | 32 |
| 600–699 | 2 | 84 |
| 700–799 | 1 | 40 |
| 800–899 | 1 | 33 |
| 1300–1399 | 1 | 36 |
| 1500–1599 | 2 | 29 |

The counts overlap by game and describe available human moves, not an independent or representative Chess.com population. The data is strong enough to explore 400–499 behavior, sparse above that, and does not cover 900–1299.

### Earlier move-fit comparisons

These are results recorded from earlier benchmark passes in this thread; they are **move-fit proxies**, not match Elo results:

- A shared 257-position pass compared the current mixture with the previous 400-to-900 linear policy. Overall NLL was 2.708 versus 2.713 and expected capped centipawn loss was 266 versus 272. Game-fold mean NLL was slightly worse for the mixture (2.664 versus 2.659). The mixture was better at 400–699 and worse at 700–899.
- A separate full 400–499 pass scored 590 opponent moves from 17 games. Current mixture NLL was 3.173 versus 3.182 for the prior policy; expected capped loss was 416.5 versus 419.5 centipawns, compared with 432.9 for the human moves. The current policy selected the top candidate 9.1% of the time versus 16.6% observed. Its predicted rate of gaps at least 300 cp was 50.3% versus 51.3% observed; at least 500 cp, 40.6% versus 43.0%. It overproduced moderate errors: gap at least 50 cp was 77.1% versus 65.0% observed, and at least 100 cp was 68.0% versus 61.0%.
- A native Stockfish endpoint model was worse overall. A curved-blend parameter fit varied substantially by held-out game fold and was not adopted.

The evidence suggests the current policy is marginally better than the previous move-fit baseline at the lowest sampled band, but has a plausible shape mismatch: too few best moves and too many moderate errors. Do not infer Elo from centipawn loss alone.

### Latest interrupted scoring attempt

A new Stockfish 19 pass was started on all 692 moves from the 400–499 Elo band, using UCI Elo 1320, Skill Level 0, MultiPV 48 per search, and a fixed 0.47-second search. The engine handshake was verified. The pass reached 350/692 positions (349 observed moves matched the candidate pool) before the user paused the goal and the process was stopped. Candidate data existed only in process memory and was lost; **there is no partial result file**. The run must be restarted from the beginning if those scores are still needed.

An initial attempt failed before scoring because python-chess does not allow persistent `MultiPV` configuration; the corrected call passes `multipv=48` to each `engine.analyse` call. A separate run that does not persist each position's candidates or progress should be avoided.

## Verification already run

- The two policy unit tests passed: rating-mixture weights and Black-to-move candidate ordering (`2 tests, OK`).
- Earlier in this work, the full `test_review_analysis` suite was reported passing (130 tests). Re-run it after any policy change; the current handoff turn did not change product code.
- The Stockfish 19 UCI handshake succeeded with local process access. A default sandbox launch stalled before handshake; the approved local-process invocation worked.

## Instructions for the next agent

1. Inspect the current worktree before editing. It was already dirty at handoff: `offline_chess/chesscom_data.py`, `offline_chess/engine_worker.py`, `offline_chess/ui/main_window.py`, `offline_chess/ui/profile_dialog.py`, and `test_review_analysis.py` were modified, and `.gui_audit_profile_20261103/` was untracked. Preserve unrelated changes and do not reset or overwrite these files.
2. Re-run the 400–499 scoring pass, persisting each row (game ID, player Elo, FEN, played move, candidate moves, scores, and candidate coverage) to a task-specific artifact as it is scored. Group folds by game, not by individual move, to avoid position leakage. Verify candidate coverage and report games/positions per fold.
3. Compare at least the current mixture, the previous policy (`non_best_chance = .10 + .78 * softness`, with weaker-candidate weights `1 / rank**1.35`), and a category-calibrated candidate distribution. The explored gap categories were: 0, 1–50, 51–100, 101–300, 301–500, and >500 cp. Fit category multipliers only on training games and evaluate them on held-out games. Use regularization and check each fold so one game cannot dominate.
4. Keep or implement a calibration change only if held-out results improve consistently and the mistake-category shape moves toward observed human play. Report NLL, candidate coverage, capped expected cp loss, top-move frequency, and rates for several loss thresholds. Do not select by a pooled score alone when game-fold results disagree.
5. Seek additional PGNs, especially 900–1319 Elo. The user was previously asked for this coverage and no answer was recorded. The current local archive has only 36 moves at 1300–1399 and none in 900–1299; it cannot support that range's calibration.
6. Validate displayed Elo with repeated games, not just move-fit metrics. Use fixed, documented time controls and engine versions; estimate result-based performance with uncertainty and compare rating bands separately. Include draw and color effects. A move-selection model can fit game moves while producing a different match Elo.
7. Run the focused policy tests and the full review test suite after modifications. Record exact commands, engine configuration, dataset fingerprint, and results so future comparisons are reproducible.
8. Leave this goal active or paused until high-band data and match-based strength validation are complete. Do not claim the whole objective is done based on the 400–499 proxy.

## Files relevant to the work

- `offline_chess/engine_worker.py` — bot move search and candidate sampling.
- `offline_chess/config.py` — Elo limits (`MIN_ELO=1320`, `MIN_TARGET_ELO=400`).
- `test_review_analysis.py` — current policy tests.
- `test pgns/chess_com_games_2026-11-01.pgn` — local archive sample used for the latest move-count audit.
