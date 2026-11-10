# Changelog

## [v3.0.0.0] - 2026-11-10

### Added

- Train today: a local queue of up to five due positions from newly reviewed mistakes and opening drills, with saved recall results and progress by topic. Correct recalls return after 1, 2, 4 days and longer; missed or revealed answers return after ten minutes.
- Board-based opening recall with a repertoire hint, replacing multiple-choice answers.
- Balanced, Attacking and Solid bot styles, plus optional between-game difficulty adjustments after completed local games. Bot ratings and style effects remain approximate.
- Resumable bot match and PGN-position benchmarks with engine/data fingerprints, candidate coverage, game-level folds, saved results and match-score uncertainty.
- Cross-platform pull-request test workflow.

### Changed

- Downloaded-game library pages and filters query SQLite for at most 100 summaries; full review data is loaded for selected games. Search is debounced and no longer rebuilds profile insights.
- Extracted training persistence, daily training UI, bot sampling and library queries into separate modules. Clearing all profile data also clears training positions and recall history.
- Release builds discover all test modules. Branch pushes verify builds; tags and manual dispatch publish releases.

### Validation limits

- The six-game bot run is a smoke check, not Elo calibration. Representative human PGNs, especially 900–1319, and longer match runs are still required.
- The synthetic 10,000-reviewed-game benchmark measures database query results, not full profile-insight rendering.

## [v2.1.1.2] - 2026-11-07

### Added

- Chess.com profiles can switch between saved usernames from the profile entry field.
- Quick PGN data areas summarize time management, opening development, trade patterns, draw and repetition results, and simplified endgames without Stockfish.

### Changed

- Quick castling scans now classify games by the user's castling, not the opponent's; old scans are refreshed.
- Batch Chess.com reviews can run deeper confirmation for likely Brilliant sacrifices while retaining quick per-move analysis.

### Fixed

- Windows release builds explicitly bundle the Python build's Microsoft Visual C++ runtime DLLs to reduce missing-dependency failures while loading `python312.dll`.
- The updater rolls back to the previous executable when the updated app exits or displays a startup error dialog.

## [v2.1.1.1] - 2026-11-06

### Changed

- Chess.com profile refreshes and PGN-derived opening insights run asynchronously so large local libraries keep the profile responsive.
- Background PGN-stat scans show local progress without blocking the profile with a full-page loading overlay.

### Fixed

- Find archives no longer leaves a loading overlay active when the archive lookup starts loading the latest month before the first worker finishes.
- Profile and Opening Trainer loading overlays are released after render failures and stale worker results.
- Closing a profile prevents background workers from scheduling another refresh.
- Chess.com game removal and saves preserve PGN/database consistency when database operations fail.
- Cached PGN statistics and partial Chess.com game records handle malformed or missing data safely.

### Verification

- 176 unittest cases passed, including local dashboard HTTP checks.
- A read-only profile timing probe confirmed PGN header enrichment completes after initial rendering.

## [v2.1.1.0] - 2026-11-05

### Added

- Profile → Settings can switch the app interface between English and Czech.
- Endgame Trainer shows a checkmate failure dialog with an option to restart the original position.
- Main tabs display an animated shadow loading card while profile and opening data load asynchronously.

### Fixed

- Opening Trainer matches Chess.com history to repertoire lines by ECO code, even when opening names differ between databases.
- Opening Trainer no longer rebuilds its full repertoire list on every visit, and adds large choice lists in batches.

## [v2.1.0.0] - 2026-11-05

### Added

- Endgame Trainer with a Position Lab-style piece editor, FEN setup, random queen, rook, and pawn endgames, and checkmate challenges against Stockfish at a selectable Elo.
- Game Review can send a legal queenless endgame position from the reviewed game into Endgame Trainer for a rematch against Stockfish.

## [v2.0.0.1] - 2026-11-05

### Added

- Opening Trainer tab beside Position Lab, with White and Black move practice across multiple repertoire lines and opponent replies.
- Chess.com opening performance table showing saved game counts and win/draw/loss results, with practice shortcuts for most-played and lowest-win-rate openings.
- Review playback follows recorded PGN clock times when available and uses a one-second fallback otherwise.
- More calibrated Stockfish candidate selection for bot ratings below 1320 Elo.

## [v1.1.5.0] - 2026-11-04

### Added

- Embedded Profile in the main app window, with a tab visible from startup.
- Compact quick-access insights calculated directly from saved Chess.com PGNs, including move, capture, check, castling, promotion, position, clock, and time-per-move statistics. These summaries do not require engine review.
- A pulsing shadow loading frame on Profile pages while page data, profile refreshes, archive requests, or background PGN scans are in progress.

### Changed

- Profile preferences and Chess.com data are now available as tabs inside the main window instead of a separate pop-up.
- Time-per-move statistics use PGN elapsed-time annotations when present and can derive elapsed time from clock annotations and the game's time control.
- Removed the Eval Tags insight, which was not provided consistently by imported PGNs.

### Verification

- All 124 `test_review_analysis` tests pass. The full 158-test local suite has two dashboard HTTP tests blocked by this environment's socket restrictions; the remaining tests pass.
- The release workflow runs the test, Windows packaging, Linux packaging, and release publishing jobs when this tag is pushed.

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
