# v3.0.0.0 validation

The three upgrade paths were implemented in order: daily training, bot practice/evaluation, then library queries and PR checks. This release does not claim the unfinished historical bot calibration goal is complete.

## Checks

- `QT_QPA_PLATFORM=offscreen python -m unittest discover -p 'test*.py' -q`: 189 tests, passed; two Windows-only updater tests skipped on Linux. Release/PR Windows jobs exercise those tests.
- `STOCKFISH_PATH=/tmp/stockfish/stockfish-linux-x86-64-universal python tools/gui_entrypoint_audit.py`: 254 audit rows, zero errors, 13 intentional skips. Stockfish 19 ready, 3,865 openings loaded, worker stopped cleanly. Temporary profiles only; no personal game data used. Includes Train today navigation, existing import/export, engine response and partial-review cancellation paths. CSV: `GUI_ENTRYPOINT_TIMINGS.csv`.
- New regression cases cover schedule persistence, failed/revealed recalls, Black-to-move review positions, board-based opening answers, bounded adaptive targets, legal seeded style sampling, Unicode/literal search, page boundaries, legacy ECO names, lazy full-record retrieval, and clearing training data.
- Offscreen previews at 1100 × 760 verified the new daily training and opening boards render with their controls. Visible OS/DPI behavior is outside this check.

## Library benchmark

`python -m tools.library_benchmark --games 10000 --output benchmarks/v3-library.json`

Synthetic 100-ply reviewed games, 23,216 bytes of review JSON per game:

| Query | Elapsed | Result bytes | Rows |
|---|---:|---:|---:|
| All full records | 774.74 ms | 252,688,890 | 10,000 |
| Filtered summary page | 16.61 ms | 41,083 | 100 |

These are single local measurements, not cross-machine performance promises. The new library query is bounded; the existing full-profile insight aggregation still loads wider data and remains a separate optimization opportunity.

## Bot benchmark

`python -m tools.bot_benchmark matches --engine /tmp/stockfish/stockfish-linux-x86-64-universal --output benchmarks/v3-bot-smoke.jsonl --games 6 --nodes 100 --max-plies 300`

Six completed games, alternating colors; balanced 800 target against the specified Stockfish 1320 reference. No truncations. Score 0.4167; conservative 95% score interval [0, 0.9711]. Repeating the command verified completed games resume without duplicate rows. JSONL records engine binary fingerprint, configuration, outcomes and moves.

This very small, low-node run validates the runner only. It does not establish an 800 Elo playing strength, nor equivalence to Chess.com ratings. Runtime play uses time limits, while benchmarks deliberately fix node budgets. Sampling is seeded; Stockfish's own weakening can still vary. PGN-position evaluation supports persisted candidate coverage and game-based folds, but no representative human PGN dataset is included in this repository. Human move-fit validation (especially 900–1319) and larger match runs remain necessary before claiming calibrated ratings. Style effects likewise remain uncalibrated.
