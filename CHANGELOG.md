# Changelog

## [v1.1.4] - 2026-11-04

### Fixed

- The Chess.com insights panel now clears its initial loading message after local insights finish rendering without overwriting active archive-operation status.
- Period comparisons no longer repeat the words “score” or “accuracy” when those values are unavailable.
- New game clears stale moves from the Play move table.
- The game board now shrinks to fit at the supported 1100px minimum window width.
- Failed Stockfish startup closes a partially launched process; stale engine results are ignored, and window close waits for a busy engine thread to finish.
- Position Lab explains checkmate and stalemate states while keeping move tables blank.
- Elo history charts discard malformed rating rows while retaining valid data.
- Empty PGNs preserve the current game, malformed PGNs show an inline warning after importing a legal prefix, and invalid UTF-8 has a readable error.

### Verification

- Added regressions for Chess.com initial-load status handling, unavailable comparison values, New game move-table reset, safe errors from game and annotated PGN exports, and review cancellation handling.
- All 150 tests pass in 6.314s when local sockets are permitted for dashboard coverage.
- The isolated offscreen entry-point sweep completed 171 rows: 154 OK, 17 intentionally skipped, 0 errors/timeouts; it verified engine readiness, legal reply, Position Lab analysis, PGN import/export, partial-review cancellation recovery, New game board rendering, and engine-thread shutdown.
- An isolated Windows PyInstaller 6.22.3 one-file build completed successfully (135,011,893 bytes) with Python 3.14. The release workflow’s Python 3.12 Windows/Linux matrix builds run after the release tag is pushed.

## [v1.1.2] - 2026-11-03

### Added

- Position Lab searches the selected side to move for up to three candidate moves within the chosen time budget.
- PGN imports report a malformed movetext token inline when a legal prefix can be recovered.

### Changed

- Empty and header-only PGNs are rejected without replacing the current game.
- Opening labels and opening-ply detection reuse an index built when the catalog loads.
- Level-two practice hints reuse the existing move-event analysis on quiet positions.

### Fixed

- Game status clears after Stockfish finishes a reply and returns control to the player.
- Game review progress and personalized explanations no longer fail due to missing standard-library imports.
- Imported games show whose turn it is and whether Stockfish is waiting or actively thinking.
- Invalid UTF-8 PGN files show a readable import error and leave the current game unchanged.

### Verification

- Added regression tests for review callbacks, opening-index equivalence, practice-hint reuse, PGN edge cases, and game-turn/status behavior.
- Recorded GUI entry-point smoke timings and a live GUI reliability audit in `GUI_RELIABILITY_AUDIT.md`.
- The release test modules pass all 127 tests on Windows when local loopback sockets are permitted for dashboard HTTP coverage.
