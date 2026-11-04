# Changelog

## [v1.1.4.2] - 2026-11-04

### Added

- Count-only Discord notifications for new installation check-ins and feedback submissions. Submitted text and identifying fields remain available only in the encrypted private telemetry store and local dashboard.

### Changed

- Updated the first-run reporting disclosure, feedback dialog, Profile settings, and README to describe the Discord notification metadata.

## [v1.1.4.1] - 2026-11-04

### Added

- Profile → Settings centralizes persistent sound effects, Lichess and weighted-book loading, bug reports and feature suggestions, usage check-in sharing, and deletion of saved game data.

### Changed

- Sound preference is saved locally. Database and feedback controls moved from the main toolbar/game controls into Profile → Settings.
- The update version parser accepts an optional fourth hotfix component so three-part installations can recognize a release such as v1.1.4.1.
- Users on v1.1.4 must download and replace the EXE manually for this hotfix; that build cannot recognize a four-part tag or run its updater helper correctly.

### Fixed

- The Windows update helper no longer launches PowerShell with `DETACHED_PROCESS`, which caused it to exit without running the encoded replacement script. The downloaded application can now replace the old executable and restart.
- Loaded Lichess opening data refreshes the Chess.com opening insights in an open profile.

### Verification

- All 152 local tests pass with loopback access enabled for dashboard tests.
- A Windows process-launch probe reproduced the updater failure with `DETACHED_PROCESS` and confirmed the hidden, non-detached PowerShell command executes.
- A Windows PyInstaller 6.22.3 one-file build completed successfully with Python 3.14.4 (135,013,308 bytes).

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
