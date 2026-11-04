# GUI reliability audit handoff

## Goal

The GUI reliability audit, fixes, verification, push to `master`, and v1.1.4 publication are complete. This handoff records the finished state and the remaining audit coverage limits in `GUI_RELIABILITY_AUDIT.md`.

## User decisions and constraints

- Continue with the already-entered Chess.com username and current data. Do not put the username in the report or final response.
- Use README and current UI as the provisional behavior reference.
- Position Lab selected-side-only output is intentional.
- Reject empty/header-only PGNs while preserving the current game.
- Import the legal prefix of malformed PGN movetext and show an inline warning.
- The user initially said v1.1.4 contains the re-handoff fixes. Read-only checks found no public tag/release at the start of this continuation. The user then explicitly authorized pushing local state to `master` and releasing v1.1.4 after the audit; this was completed and independently verified.
- Disposable profile and read-only public requests were approved. Privacy/reporting choices were made by the user; do not change reporting settings or submit telemetry/feedback.
- The populated GUI profile's isolation is not established. No Chess.com downloads, saved-game reviews, data deletions, or profile-setting changes are authorized. Read-only list inspection and public archive GET requests are authorized.
- Never delete or alter stored profile data without separate action-time authorization.
- Release complete: commit `080677eea453e34bcf899a4a532befa3dce4af48` is on `master` and tagged `v1.1.4`; GitHub Actions run [41896637890](https://github.com/JIMMYJAMTHEVERYFATMAN/Chess/actions/runs/41896637890) succeeded; the [v1.1.4 release](https://github.com/JIMMYJAMTHEVERYFATMAN/Chess/releases/tag/v1.1.4) is published with all nine expected assets. No further release action is pending.

## Current worktree

- Modified: `offline_chess/ui/profile_dialog.py`, `offline_chess/ui/main_window.py`, `offline_chess/engine_worker.py`, `offline_chess/config.py`, `test_review_analysis.py`, `tools/gui_entrypoint_audit.py`, tracked `GUI_ENTRYPOINT_TIMINGS.csv`, `CHANGELOG.md`, and `GUI_RELIABILITY_AUDIT.md`.
- Untracked: `.gui_audit_profile_20261103/` and `GUI_RELIABILITY_HANDOFF.md`. The `.gui_audit_profile_20261104/` artifacts may be ignored by Git; preserve disposable profile/build data unless its contents and provenance are checked.
- `offline_chess/config.py` now reports `APP_VERSION = "1.1.4"`. The target was selected after confirming that v1.1.2 and v1.1.3 are already tagged.
- Latest full test command: `.venv\Scripts\python.exe -m unittest discover -p "test*.py" -q` — **150 tests in 6.314s, OK**, with local IPC/socket access. The restricted environment blocks local sockets used by dashboard tests and Python's asyncio process bridge; the approved local-IPC retry passed. `git diff --check` passes with LF-to-CRLF warnings only.
- Latest isolated offscreen GUI sweep: `tools/gui_entrypoint_audit.py` completed 171 rows (154 OK, 17 intentionally skipped, 0 errors/timeouts) with local IPC/socket access. It used a temporary profile and disabled reporting; it verified Stockfish startup/reply, Position Lab analysis, PGN import/export, partial-review cancellation recovery, New game widget rendering, and clean engine-thread shutdown. A visible screenshot of the user-started app showed Stockfish ready, the clear starting board, and blank move list; the UI was returned to Game. `GUI_ENTRYPOINT_TIMINGS.csv` contains no personal profile data.
- A fresh isolated PyInstaller 6.22.3 Windows one-file build succeeded with Python 3.14 (135,011,893 bytes). The release workflow's pinned Python 3.12 Windows/Linux matrix runs after the v1.1.4 tag is pushed.
- At the start of this continuation, read-only GitHub checks found no v1.1.4 tag/release and `master` remained `6c10a2e` (`Prepare Offline Chess v1.1.3`). Historical workflow run [41877590914](https://github.com/JIMMYJAMTHEVERYFATMAN/Chess/actions/runs/41877590914) built v1.1.3 but failed publication because the tag lacked a matching changelog heading. The local v1.1.4 heading now matches the release workflow gate; the user authorized publishing after all audit gates pass.
- Computer Use captured the PyCharm-started GUI successfully: Stockfish reported ready, and the game board/history were at the starting position. It was returned to the Game tab. The OS window remained maximized, so visible border-drag/DPI checks remain pending; 1100px minimum geometry is covered by the offscreen real-window regression.
- Matrix in the audit report: **23/27 component groups touched, 0/27 fully verified** under the strict live-behavior definition; every row states its remaining evidence limit. The 1100px minimum uses a 360px board minimum; geometry regressions verify containment/no overlap at 1100×760, 1280×800 and 1600×900. Visible OS border-drag/DPI coverage remains limited. The static code map covers all tracked modules; behavior tests focus on important game/UI/worker paths rather than a separate runtime test for every function.

## Fixed finding: stale move rows after New game

A live Play snapshot originally appeared to show stale move rows. `ChessWindow.reset()` now clears both the Play and Review tables; the focused Qt regression verifies zero rows. A later visible replay used a disposable profile and synthetic `e4 e5` game. After New game, the status/history and move list reset; the user directly confirmed the board displayed all pieces on their starting squares. Two screenshots continued to show stale pawns, which are capture artifacts, not a visible product defect. The offscreen harness also confirmed the widget FEN and rendered board reset.

## Completed fixes this turn

1. Profile Chess.com initial loading status: wrapped its deferred local library refresh in `_finish_chesscom_initial_load()`, changing only the exact initial loading message after refresh. This avoids overwriting active archive-operation progress. Regression covers empty profile, one downloaded game, and preservation of an active operation status.
2. Duplicate Chess.com comparison fallback labels: score/local accuracy fallbacks now use `unavailable` without repeating labels. Regression reproduces the previously duplicated words.
3. New game now clears the stale Play move table; regression uses real Qt tables.
4. Game and annotated PGN export write failures were exercised with a temporary directory as the invalid destination; both show an error and preserve board/history. Actual ACL-denied destinations remain untested.
5. Review cancellation request and worker pre-first-ply cancellation completion have focused regressions. The isolated sweep canceled an eight-ply synthetic review after progress; cancellation during a blocking live engine search remains unverified.
6. The main board minimum width is reduced to 360px so the side panel fits at the existing 1100px window minimum; a full-window geometry regression covers three sizes.
7. Engine startup now closes a partial UCI process on configuration failure and shares graceful-quit/force-close cleanup with normal shutdown. Tests cover missing engine, post-launch configuration failure, and quit failure fallback.
8. Stale bot callbacks no longer clear the busy state of a newer game. Best-move and evaluation results now carry position context into guarded UI slots; the old worker signals still emit their original payloads and remain available, while the main UI uses only the guarded signals. Three focused regressions cover the old-callback race and signal compatibility. The old direct UI connection to the integer-only evaluation signal was removed, so stale evaluations cannot bypass the new FEN guard. A seven-run, 200,000-callback microbenchmark measured 0.062 µs/callback before versus 0.083 µs after the stale-result guard (about +0.021 µs absolute; not user-visible and no speedup claimed).
9. Window shutdown now defers closing if the engine QThread remains active after the existing 2.5-second wait. A `thread.finished` callback closes it later; shutdown is requested only once. The regression failed before the fix, then passed with the 147-test suite. This prevents destroying the window while its worker thread is still running.

Fix alternatives and evidence are in `GUI_RELIABILITY_AUDIT.md`; user-facing changes are in `CHANGELOG.md`.

## Current user-directed Chess.com data

- Current local dataset: 131 downloaded, 0 move-by-move reviews.
- All time snapshot was restored: 131/131 games, 75 wins / 7 draws / 49 losses, 60% score, 57% win rate, 56.1% Chess.com accuracy (15 games), 0/131 reviewed.
- Search “Sicilian”: 6 results; Not reviewed: all 131; Next page showed rows 101–131 and disabled Next; Previous returned to rows 1–100. Search, filter, page and All time period were restored; no rows were selected.
- Read-only public archive lookup worked with no month selected. November 2026 returned 54 games; October returned 77. No archive PGNs were downloaded.
- A prior separate 2,938-game / 90-reviewed profile snapshot appears in the historical report; keep it clearly separated from the current 131/0 dataset.

## Remaining audit scope

Read `GUI_RELIABILITY_AUDIT.md` first. It contains the 27-group matrix, live interaction history, architecture/dependency map, optimization benchmarks and alternatives, source/test evidence, and remaining live-UI limits. The user chose to keep valid Elo chart records and show blank Position Lab tables with an explanatory status for terminal positions; both behaviors are implemented and regression-tested. The synthetic library benchmark was 20.7 ms for 131, 78.5 ms for 2,938, and 233.2 ms for 10,000 unreviewed no-PGN games; a profile refactor needs reviewed/PGN-heavy evidence. Avoid profile mutations and downloaded-game opening/review. Temporary-directory PGN export failures have been exercised offscreen; actual ACL-denied paths remain untested.

## Completed acceptance

- Verify all reproducible critical/high defects are fixed or documented with a concrete blocker.
- Update exact current GUI coverage and distinguish live verification from offscreen-only tests.
- `git diff --check` and staged-diff checks passed before release. The changelog/version and workflow asset gates passed.
- The full local suite passed 150 tests; isolated GUI sweep completed 171 rows (154 OK, 17 intentionally skipped, 0 errors/timeouts); local Windows one-file packaging succeeded.
- GitHub Actions completed successfully for Windows, Linux amd64, and Linux arm64. The remote `master`, annotated tag, release URL, and nine release assets were independently verified; see the final gate record in `GUI_RELIABILITY_AUDIT.md`.
- Remaining work is limited to optional live GUI coverage listed in the audit matrix; no known critical/high defect is awaiting a fix. Do not reopen data-mutating checks without fresh authorization.
