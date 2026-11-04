# Changelog

## [v1.1.3] - 2026-11-04

### Added

- Chromebook Linux packages for amd64 and arm64.

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
