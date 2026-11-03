# GUI reliability audit

Status: in progress. This audit uses a disposable `LOCALAPPDATA` profile. Normal GUI tests use the README and visible UI as the provisional behavior reference. Only read-only public network requests are authorized; telemetry submissions and other data transmissions are excluded. In-app data deletion requires a separate confirmation immediately before the action. The agent will not change in-app security or privacy settings.

## Environment and initial findings

- Windows desktop, Python 3.14, PyQt6, `python-chess`, bundled Stockfish 19.
- In the restricted process sandbox, Python 3.14's asyncio Proactor event loop blocked creating its local socket pair. The Stockfish GUI consequently reached its 30-second startup warning. A direct UCI pipe to the same binary completed in 0.43 seconds. An approved unsandboxed handshake completed in 0.407 seconds and reported 19 options; the live GUI then displayed “Stockfish 19 · ready”. This is an environment restriction, not a reproduced product defect.
- The first-run usage-reporting consent dialog blocked the live GUI; the user dismissed it manually. No telemetry POST or other transmission was triggered.
- Existing `tools/gui_entrypoint_audit.py` is an offscreen smoke sweep. It stubs dialogs/file pickers and skips worker-intensive buttons, so it does not satisfy the live GUI, error-path, or stress portions of this audit.

## Architecture and dependencies

- Repository inventory: 31 Python files, including tests and maintenance scripts; 28 non-test Python files contain 10,269 source lines. AST inventory found 52 classes and 656 functions/methods across the Python files.
- `main.py` is the runtime/compatibility entry point. UI pages and windows live under `offline_chess/ui/`; chess algorithms, Stockfish work, opening data, Chess.com import/storage, local dashboard, telemetry, and configuration are separated into core package modules. Top-level updater and dashboard/key-management scripts handle release delivery and local telemetry administration.
- Declared runtime dependencies in `requirements.txt`: PyQt6 6.9.1, python-chess 1.999, chess 1.11.2, and cryptography `>=44,<48`; Stockfish 19 is supplied as a separate engine executable. The three unittest modules cover review, release hardening/dashboard, and telemetry routing.
- The codebase is not fully function-by-function audited yet; the function inventory is a navigation aid. This document tracks the live reliability coverage and the measured optimizations inspected so far.

## Component matrix

Progress is measured by component groups touched, not by individual widget count. A group is fully verified only after its planned visible behavior and relevant state/result have been observed. Current touch coverage: **23 / 27 groups (85.2%)**; fully verified coverage: **0 / 27 groups (0%)**. The concrete control inventory and live interaction log below distinguish pending checks from observed checks.

| # | Component group | Planned checks | Status / evidence |
|---:|---|---|---|
| 1 | Startup and engine lifecycle | Ready, delayed, unavailable, recovery, clean shutdown | Partial: elevated startup reached “Stockfish 19 · ready”; closing the window under normal engine readiness exited with code 0. Under the restricted delayed-start condition, the window disappeared but the launch session returned code 1 with no trace; that case remains unresolved |
| 2 | Global header | Tablebase menu, opening/book pickers, profile, feedback, fullscreen | Partial: Profile opens/dismisses; Lichess, weighted-book and tablebase pickers open/cancel; fullscreen exit works; bug report dialog inspected/canceled without sending; feedback submit excluded |
| 3 | Main tabs | Game, Position Lab, Puzzles navigation and repeated switching | Partial: Position Lab and Puzzles selected in live GUI; each showed its controls and state |
| 4 | Chessboard game input | Select, legal/illegal moves, deselect, promotion, repeated clicks | Partial: two legal human moves accepted; Stockfish returned legal replies |
| 5 | Game actions | New game, undo, resign cancel/confirm | Partial: Undo removed the two-ply human/engine pair and restored the initial board; in the delayed-engine run, New game worked, Undo at zero plies was a no-op, and resign confirmation displayed then Cancel left the game active |
| 6 | Engine game actions | Best move and evaluation | Partial: Best move displayed an arrow without playing the move; evaluation not tested |
| 7 | Game settings | Side, Elo slider extremes, sound toggle, board flip | Partial: Black selection started an opponent move and flipped the board; sound was toggled off and restored; board orientation was flipped and restored; White-side switch warned that it would clear the current game, and Cancel preserved it; Elo extremes pending |
| 8 | Review filters and move table | Filter combinations, empty results, row navigation | Partial: populated four-ply move table appeared after live review; filters and row navigation remain untested |
| 9 | Review workflow | Start, progress, cancel, completion, repeated requests | Partial: review started, progressed, and completed on a two-ply live game after fixing two missing imports; cancel and repeated requests pending |
| 10 | Review analysis surfaces | Chart, move detail, best/reply, PV stepping, key moments | Partial: summary opened, “Review every move” entered move navigation, and Next key moment changed selection; chart filters, best/reply lines, and export remain pending |
| 11 | Coach actions | Review, practice, opening explorer, puzzle studio | Partial: empty state and reviewed-game lesson summary rendered; Retry showed the expected guards for no review and no missed chances; opening explorer/puzzle actions not completed from Coach |
| 12 | PGN import/export | Valid, malformed, empty, large, missing and unwritable files | Partial: valid 78-ply game loaded; export picker cancel was safe; empty/header-only PGN rejects and preserves the current game; malformed movetext imports its legal prefix with an inline warning; multi-game import selected only the first game; 1,000 plies and a 1 MiB comment imported; invalid UTF-8 now shows a readable error and preserves the current game after a follow-up fix/test; missing/unwritable files remain untested |
| 13 | Position Lab board and FEN | Edit/place/remove/move, valid/invalid FEN, clipboard, reset/clear/flip | Partial: placement, Move mode, moving, square removal, reset, clear, and flip observed; invalid FEN showed a parser message and left the board unchanged; valid FEN/clipboard remain untested |
| 14 | Position Lab options | Side, time budget, castling toggles and synchronization | Partial: Black-to-move changed the visible panel and label; 2s selected; one castling right toggle reset stale analysis and updated status; Reset restored rights |
| 15 | Position Lab results | Analysis, candidate selection, PV stepping, terminal/no-legal-move positions | Partial: three candidates returned within the 2s limit, second candidate changed the preview, Forward advanced the PV; invalid one-king position rejected with guidance; top results stayed tied to the analyzed root while preview stepped through the PV |
| 16 | Puzzle setup | Modes, focus selectors, difficulty and mate depth boundaries | Partial: inspected the five modes; Checkmate in N exposed its mate-depth control; Engine Focus and Total Random generated puzzles; other modes and option bounds remain untested |
| 17 | Puzzle lifecycle | Generate, solve, lives, hint, reveal, challenge and failures | Partial: Engine Focus and Total Random puzzles generated; Hint displayed tactical guidance without consuming a life; Show solution revealed Ng3 and the fork line without consuming a life; Checkmate in N depth 1 and 5 bounds displayed; challenge scoring and wrong-move failures pending |
| 18 | Profile overview filters/table | Date/result/color/opening/opponent filters, selection, open | Partial: overview opened with no saved games and displayed the empty-state message; filters/selection not tested |
| 19 | Profile appearance | Theme, piece style, coordinate and animation toggles | Partial: inspected all 14 board themes and 5 piece styles; retained Classic Green/Tournament; coordinate and animation toggles were checked and left unchanged |
| 20 | Profile storage and deletion | PGN preference, selected/all deletion and confirmations | Partial: Delete all profile data confirmation appeared and Cancel dismissed it; no data was deleted. Selected-game and stored-PGN deletion were not attempted because the disposable profile contained no entries |
| 21 | Reporting and privacy controls | Reporting consent/toggle and feedback transmission | User dismissed the fresh profile's first-run dialog; no privacy control was changed by automation and no telemetry or feedback was transmitted |
| 22 | Chess.com account/archive controls | Username validation, archive discovery, month selection | Partial: blank username showed local format validation; the empty Chess.com panel rendered without a username, and no account lookup was issued |
| 23 | Chess.com library actions | Download/update, lookup, filters, paging, review, cancellation | Not started: download/update stayed disabled without a username or archive data; writes would be confined to disposable profile; public GET requests only |
| 24 | Feedback dialog | Input validation and cancellation | Partial: dialog reviewed and canceled; submit was not attempted because it would transmit the message, app version, install ID and saved Chess.com username |
| 25 | Review/practice/explorer dialogs and keyboard | Modal paths, navigation, F11/Escape, arrow keys | Partial: review summary and move-by-move/key-moment navigation completed; practice/explorer dialogs and keyboard shortcuts remain |
| 26 | Resizing and display density | Min/max window, narrow layout, repeated resize | Not started |
| 27 | Rapid input and lifecycle stress | Repeated clicks, simultaneous requests, close during work, recovery | Partial: standard-ready close returned code 0; restricted delayed-start close removed the window but returned code 1 with an empty trace. Broader rapid-input stress remains untested |

## Live interaction log

## Concrete GUI control test strategy

The component matrix is the execution ledger. This control inventory makes its planned scope explicit; **Pending** means no live result is claimed. Combinations prioritize transitions that can invalidate state or overlap asynchronous engine/network workers.

| Surface | Controls included | Planned cases and state combinations | Execution state |
|---|---|---|---|
| Header and window | Tablebases menu; Load Lichess DB; Load weighted book; Profile; Report bug; Suggest feature; Enter/Exit fullscreen; F11 and Escape | Open/cancel every picker; missing, malformed and valid local paths; tablebase unavailable/configured; dialogs cancel; shortcut/button parity; fullscreen → windowed → resize → fullscreen | Partially exercised; consent modal currently blocks further input |
| Runtime updater | Automatic release check; update-available prompt; download progress/failure; install/relaunch | Read-only public release check is within scope; downloaded installer execution/replacement is a separate high-impact action and is excluded without action-time authorization; local non-frozen run does not enter the packaged-app update path | Not exercised; install/relaunch explicitly excluded |
| Game board and actions | 64 board squares; move list; New game; Undo; Resign; Best move; Import PGN; Save PGN | Legal/illegal selection, deselection, capture, promotion, terminal state; board/table navigation; undo from zero/one/two plies; resignation cancel; best-move no-commit; valid/empty/malformed/multi-game/large/unreadable/unwritable PGNs; picker cancel | Core moves, undo, best-move and PGN cases partially exercised; full edge matrix pending |
| Play settings | Play as White/Black; opponent-strength slider (400–3190); Sound effects; Flip board | Slider min/max/intermediate values and rapid drag; side switch with empty/active game and cancellation; sound/flip while thinking/reviewing; verify position/history consistency | Side, sound and flip partial; slider bounds pending |
| Main modes and review | Play/Review/Coach; move-type filter (10 options); side filter (4 options); analysis depth (Quick/Standard/Thorough); review start/cancel; move table; chart filters; best/reply overlays; PV stepping; key-moment navigation; spoken review; export | Exhaust filter combinations on empty and populated review; navigate during analysis; repeated start/cancel; compare board/table/metric consistency; toggle overlays and PV at endpoints; speech unavailable; export cancel and invalid path | Review completion and key-moment navigation observed; filters, cancellation/repeat, chart/export and speech remain pending |
| Coach and practice dialogs | Retry mistakes/missed chances; opening explorer; puzzle studio; Hint; Show best move; Play best line; Next position; Flip board; Close | Empty and populated review; no-mistake and missed-chance games; legal line completion; repeated hints/reveal; next-position boundaries; dialog close while engine work runs | Empty-state guards and puzzle hint/reveal partially exercised; line play, close-during-work and explorer actions pending |
| Position Lab | Editable board; white/black piece racks; Move tool; FEN field; Load FEN; Copy FEN; Reset; Clear; Flip; White/Black to move; four castling checkboxes; time choices; Analyze; three candidate rows; previous/next PV | FEN valid/invalid/partial/Chess960; paste/copy; piece replace/remove/move/promotion; impossible rights; terminal positions; each time limit; analyze twice, edit during work, candidate/PV endpoints | Most board-edit and selected-side analysis paths partial; valid FEN/clipboard, time bounds and concurrent request transitions pending |
| Puzzle setup and lifecycle | Mode (five choices); focus type; skill; phase; piece type; mate depth 1–5; difficulty 1–10; Find puzzle in game; Generate puzzle; Hint; Show solution; challenge controls | Min/max values, fast repeated generation, unavailable engine, no eligible positions, each mode/source, correct/wrong move, life depletion, reveal then next, challenge score/high score boundaries | Mode visibility and one generated hint/reveal case observed; remaining modes and failure/scoring paths pending |
| Profile overview | Theme (14 choices); piece style (5); coordinates; animation; date/result/color/opening/opponent filters; Keep full PGNs; Open saved game; Delete selected game; Delete stored PGNs; Delete all profile data; usage-reporting toggle; Send feedback | Filter cross-products and empty table; persistence across reopen; open summary-only/full-PGN rows; deletion cancel paths only; reporting control is excluded from automation; feedback validation/cancel only, no send | Appearance lists and summary-only guard observed; filter persistence and cancel branches pending; privacy/feedback transmission excluded |
| Profile: Chess.com | Username field; Find archives; month selector; Download all months; Update downloaded games; date/speed/shape filters; Lookup opponent countries; Stop lookups; Quick scan; available/library tables; Download selected/month; search; review filter; Previous/Next 100; Open/Review selected; Review all unreviewed; Open PGN folder; Remove selected | Local username validation; public read-only GET for a public profile; no archives/empty month/network failure; selection/paging/filter composition; cancellation during fetch/review/country scans; missing PGN; empty-selection Remove selected only (actual deletion needs action-time approval) | Blank-username local validation observed; public archive flow and controls pending |
| Dialogs and display | Feedback category/message/Cancel/Send; resign/side-switch confirmations; file pickers; review progress/summary; Profile tabs; min/max/narrow/repeated resize | Empty/overlong text, category switch and cancel; confirmation cancel branches; missing/unwritable paths; long review progress/cancel; repeated open/close; min and max sizes, narrow layout, rapid repeated resizing | Several modal cancel paths and one fullscreen exit observed; resized layout and worker interruption pending |
| Stress transitions | Rapid legal/illegal board clicks; tab/mode switching; repeated new/undo/review/analyze/generate; close while Stockfish or network worker active | Short bursts then inspect process/thread state; repeated clicks during disabled/busy states; switch tabs mid-work; close/reopen; verify no stale result mutates newer game; resource and shutdown checks | Not yet executed as a systematic stress run |

Fixtures are stored under `.gui_audit_profile_20261103/fixtures/fixtures/`. The empty, header-only, malformed-prefix, annotations/variation, multi-game, 1,000-ply, 1 MiB-comment and invalid-encoding cases were imported through the live GUI.

| # | Action | Observed result |
|---:|---|---|
| 1 | Select Position Lab tab | Tab opened; board and controls rendered |
| 2 | Select Black to move | Black radio selected; heading and result panel switched to Black |
| 3 | Select Puzzles tab | Puzzle Studio rendered |
| 4 | Click Generate puzzle while Stockfish was unavailable | No generation began; puzzle state remained Ready |
| 5 | Open training mode list | Five modes appeared: Engine focus, Total random, My Chess.com training, Checkmate in N, Challenge |
| 6 | Select Checkmate in N | Description and mate-depth control appeared; no crash |
| 7 | Play e2-e4 | Legal move accepted; Stockfish replied with Nc6; opening label updated to Nimzowitsch Defense |
| 8 | Play g1-f3 | Legal move accepted; Stockfish replied with e6; move table and opening label updated |
| 9 | Reproduce status after engine reply | Board and move table showed the reply, turn returned to White, but status remained “Stockfish is thinking…” |
| 10 | Restart with status fix and replay e2-e4 | Engine replied with Nc6; status bar was blank once control returned to White |
| 11 | Position Lab: select 2s, Black to move, Analyze | Three legal candidates appeared within the selected time limit; status said the moves were ready |
| 12 | Select second candidate, then Forward | Candidate preview changed to d5; PV stepping advanced the preview position |
| 13 | Expand castling rights; toggle Black O-O | Checkbox changed and stale candidate output cleared; status reported “Castling rights updated.” |
| 14 | Reset, Clear, then Flip | Reset restored initial pieces/turn/all four rights; Clear removed pieces and rights; Flip reversed board coordinates |
| 15 | Add white queen and king from rack; Move king; right-click queen | FEN and board reflected piece additions, king relocation, and queen removal |
| 16 | Analyze setup missing Black king | Analysis was rejected before engine work with the expected one-king-per-side/FEN guidance |
| 17 | Undo after e4/…Nc6 | Both plies were removed; board and move count returned to the starting state |
| 18 | Best move from the starting position | Green e2-e4 arrow appeared; no move was added to the game |
| 19 | Review Game with no played moves | Informational dialog asked the user to play a game or import a PGN; dialog dismissed normally |
| 20 | Review Game after e4/…e5 and e4/…Nc6 in separate runs | The app process disappeared on each attempt; process exit code was 1 on the fault-trace run and no Python fault-handler output was captured |
| 21 | Profile → Chess.com, Find archives with blank username | Local validation required 2–25 letters, numbers, underscores, or hyphens; no archive request was issued |
| 22 | Review Game with two played plies before fix | Trace captured `NameError: time is not defined` while constructing `ReviewProgressDialog`; review request stayed in a broken reviewing state |
| 23 | Run review after adding `time` import | Engine analysis completed and move grades appeared; trace then captured `NameError: re is not defined` when the review summary personalized explanation text |
| 24 | Run review after adding `re` import | “Game review complete” summary displayed; White and Black move quality and key moment were present |
| 25 | Choose “Review every move”, then Next key moment | Summary closed into move-by-move review; selected move and board position advanced to the next key moment |
| 26 | Resign → Cancel | Confirmation said the result would be recorded as a loss; Cancel closed the prompt and left the game active |
| 27 | Change player side Black → White while the opponent had played | Confirmation warned that the current game would be cleared; Cancel preserved the one-ply game and Black remained selected |
| 28 | Toggle Sound effects off and back on; flip board twice | Checkbox changed and was restored; board orientation changed while preserving the current game, then was restored |
| 29 | Open Profile → Overview, inspect board and piece style lists | Overview showed two saved test games from this isolated profile; 14 board themes and 5 piece styles appeared; current Classic Green/Tournament settings were retained; coordinate/animation toggles remained on; profile closed normally |
| 30 | Coach → Retry mistakes_missed chances without a completed review | Informational dialog said “Run a review first to build practice positions.”; dismissed with OK; game remained unchanged |
| 31 | Generate engine-focus puzzle, then Hint and Show solution | Puzzle generated with a fork tactic; hint supplied a target-attacking clue; solution revealed Ng3 and the best line; lives remained 3/3 throughout |
| 32 | Profile → Open saved game for a summary-only entry | Informational notice explained that moves were not stored because full-PGN storage is off; dismissed normally; storage setting was left unchanged |
| 33 | Play e4 e5 Nf3 d6, run Review Game, then open Coach | Review completed with move grades/key moment; Coach showed White quality 100%, opening-phase quality, focus lesson and Philidor Defense label; Retry stated there were no mistakes/missed chances to retry |
| 34 | Open and cancel Lichess, weighted Polyglot book and Syzygy tablebase pickers | Each picker opened with its expected title/type; Cancel closed it without changing the active game or tablebase-off state |
| 35 | Report bug dialog → Cancel | Dialog identified the data sent on submission (message, app version, install ID and saved Chess.com username); Cancel closed it; no data was sent |
| 36 | Exit fullscreen | App returned to a normal window with the OS title bar and an “Enter fullscreen” control; max/min density and repeated resize remain untested |
| 37 | Import empty/header-only PGN in live GUI | Error dialog said the PGN contains no moves and the current game was kept; starting board remained at the prior position |
| 38 | Import `1. e4 e5 2. Nf3 Kg9 *` after warning fix | Three legal plies loaded; status bar read “Imported 3 plies · invalid token 'Kg9'; loaded legal prefix only”; turn label and live hint both said “Stockfish to move” without suggesting a search was active |
| 39 | Fresh launch using workspace-isolated `LOCALAPPDATA`, reporting endpoint disabled | Board rendered and engine status reached “Stockfish 19 · ready”; user manually dismissed the first-run consent modal; no outbound reporting request was enabled |
| 40 | Close the app after normal engine readiness | The window disappeared and the launch session exited with code 0 |
| 41 | Close the app while restricted Stockfish startup was delayed | The window disappeared, but the host session exited with code 1 and `launch_trace.log` remained empty; this result is unresolved and is not counted as a clean close |
| 42 | Profile → Overview → Delete all profile data → Cancel | Confirmation displayed; Cancel closed it; the disposable profile was not deleted or changed |
| 43 | Profile → Chess.com tab in the empty disposable profile | Empty performance/archive state rendered; no username was entered and no public request or download occurred |
| 40 | Position Lab starting position, 2-second analysis, Forward, then re-analysis | Three White candidates appeared; Forward displayed e4 on the board while preserving the original analysis root; repeating analysis produced candidates for the same unchanged root, as indicated by the PV preview behavior |
| 41 | Position Lab invalid FEN, Reset, Clear, Analyze empty board, Reset | Invalid FEN gave inline parser feedback and preserved the board; Reset restored the initial position; Clear emptied pieces; Analyze rejected the invalid board before engine work; Reset restored the initial position again |
| 42 | Puzzle Studio: Engine Focus and Total Random | Each mode generated a local puzzle; Hint gave tactical guidance; Engine Focus solution revealed its line; no life was consumed and no saved profile data/network request was needed |
| 43 | PGN fixtures: annotations/variation, empty, headers-only, malformed token, multi-game, 1,000 plies, 1 MiB comment, invalid UTF-8 | Annotations/variation imported the mainline e4 c5; empty and header-only inputs rejected and preserved the game; malformed input imported e4 e5 Nf3 and showed the inline `Kg9` warning; only the first of two games loaded; 1,000 plies and 1 MiB comment loaded without a crash; invalid UTF-8 was rejected and preserved the prior game, but surfaced Python's raw decoding error |
| 44 | Puzzle Studio: Checkmate in N, mate-depth limits | Depth field accepted/displayed 1 and 5 moves; no puzzle generation was triggered from an unrelated random position |
| 45 | Exit fullscreen, close isolated GUI | Exit fullscreen returned to a titled window; clicking its close control closed the Chess window. Engine subprocess termination was not independently measured |
| Baseline | Import README.md with All files selected (before the PGN fix) | UI reported “Imported 0 plies” and replaced the current game with an empty starting position; superseded by interaction 37 and the user's reject-and-preserve decision |
| Baseline | Import `1. e4 e5 2. Nf3 Kg9 *` (before the warning fix) | UI accepted three legal plies and silently ignored invalid `Kg9`; turn label and thinking hint disagreed; superseded by interaction 38 and the user's allow-prefix-with-inline-warning decision |
| Baseline | Open then Cancel the PGN picker | Picker closed and preserved the current board/move list; cancellation behavior remains unchanged |
| Baseline | Position Lab: load `not a fen` | Parser error was displayed in the status area; starting board remained unchanged |

## Reproduced defect and fix

- **P3, stale game status:** After each of two legal human moves and engine replies (`e2-e4` → `...Nc6`; `g1-f3` → `...e6`), the board and move table showed the completed engine move and the turn label returned to White, but the status bar remained “Stockfish is thinking…”. A settled screenshot reproduced this twice.
- **Cause:** `ChessWindow._bot_move()` applies the engine reply and updates the turn, but never clears the temporary status message when control returns to the human.
- **Fix:** Clear the status message only when the valid engine move returns the turn to the player and the game is not over. This leaves terminal game-result messages intact.
- **Evidence:** The regression test passes; the full repository suite now passes 126 tests. Live post-fix replay of e2-e4 → ...Nc6 showed the status bar cleared after the engine returned control.

## Reproduced review failures and fixes

- **Review Game callback errors:** The first traced failure was `NameError: time is not defined` in `ReviewProgressDialog.__init__`, on the `ChessWindow.start_review()` path. After adding that import, Stockfish finished but the UI callback raised `NameError: re is not defined` inside `review_text_for_viewer()` while creating the summary. Both were missing standard-library imports in modules that directly use those names.
- **Fixes and evidence:** Added `import time` to `review_dialogs.py` and `import re` to `review_logic.py`. Added focused regression tests for dialog timer/progress initialization and reviewed-side explanation personalization. A fresh GUI session completed the two-ply analysis, displayed “Game review complete” with both sides’ grades and a key moment, entered “Review every move”, and advanced to the next key moment. The trace file contains no exception in this final run; the full repository suite passes 126 tests.
- **Severity correction:** The initial three runs looked like process termination; instrumentation proved uncaught Python callback exceptions were the cause. Review is now live-verified after both fixes; cancellation and repeated-request behavior still need testing.

## Automated test results

- Privacy-safe offscreen entry-point sweep: `python tools/gui_entrypoint_audit.py` — **168 rows: 151 passed, 17 skipped, 0 errors**. The script uses a temporary profile, prevents the first-run reporting handler from making a choice, skips the privacy checkbox, stubs file/message/desktop dialogs and links, and never submits feedback. Skips are worker-intensive puzzle/explorer actions, controls disabled in the initial state, and the reporting toggle. Startup/engine-ready was 1,084.8 ms, opening catalog load 299.0 ms, and e4-to-Stockfish reply 487.8 ms in this run. These are one-run smoke timings, not controlled performance comparisons or live full-flow results.
- Focused review suite before PGN import work: `python -m unittest test_review_analysis -v` — **89 tests passed** in 1.446s; eight import/turn tests were added afterwards.
- Full repository suite, restricted sandbox: **124 passed, 2 errors** because Windows denied loopback socket creation (`WinError 10013`) in the two local dashboard HTTP tests.
- Full repository suite, approved local unsandboxed run before the invalid-encoding message regression was added: `python -m unittest discover -q` — **126 tests passed** in 3.849s. Only the two dashboard HTTP tests bind local loopback sockets; no external service was contacted.
- After the invalid-encoding regression was added, the restricted run excluding only the two loopback HTTP tests passed **125 tests** in 2.713s. The full restricted run was also attempted and those same two tests failed at socket creation with `WinError 10013`; no external service was contacted.
- Release suite after the final PGN regression and v1.1.2 metadata/workflow updates: `.venv\\Scripts\\python.exe -m unittest test_review_analysis test_telemetry test_release_hardening -q` — **127 tests passed** in 3.830s with local loopback access. python-chess emitted expected malformed-PGN diagnostics; no external service was contacted.
- Release-note extraction check matched `CHANGELOG.md`'s `[v1.1.2]` section and extracted 1,145 characters; `git diff --check` passed.

## Optimization evidence and alternatives

### Opening lookups: reuse one position index

The original `detect_opening()` rebuilt a dictionary across all 3,865 loaded opening lines on every lookup. `find_opening_ply()` separately rebuilt a set of all 7,982 unique position keys. The main window now builds one map when the opening catalog loads and passes that map to both lookup functions. Existing call signatures still work without the optional map, and a test compares indexed lookups with uncached results.

| Approach | Speed evidence | Memory | Code and maintenance | Side effects |
|---|---|---|---|---|
| Rebuild lookup structures on every call (original) | 156.716 ms for 50 `detect_opening` prefixes; 53.752 ms for 50 `find_opening_ply` prefixes | No long-lived cache; repeats allocation and indexing work | 45 lines across the two lookup functions plus `_set_openings`; least state to maintain | Same labels and ply outputs |
| Cache a dictionary for detection plus a separate set for ply lookup | Detection can use the dictionary; cached-set membership measured at 9.030 ms versus 8.614 ms with the dictionary for the same 50 ply queries | About 0.69 MiB for the dictionary and about 0.25 MiB additional for a set (estimated shallow structure sizes) | Adds duplicate cache state and rebuild/synchronization paths | Same outputs; set was about 5% slower in this sample |
| Reuse the single cached dictionary (chosen) | 9.672 ms for detection (16.2× faster); 8.547 ms for ply lookup (6.3× faster). Building the dictionary once took 3.042 ms. Each sample used the same real catalog and 10-ply line; result labels/plies matched | About 0.69 MiB additional dictionary/tuple storage; opening-key strings and `Opening` objects are shared | 54 lines versus 45 before (+9 lines); one index lifecycle, one lookup structure, direct map membership | Same outputs; index refreshes whenever the catalog is replaced |

Measurements are local microbenchmarks with five-sample medians, not whole-app latency claims. The `GUI_ENTRYPOINT_TIMINGS.csv` file contains the measurements and setup.

### Position Lab: selected-side MultiPV

The user explicitly confirmed that the current selected-side behavior should remain. The original two-sided analysis produced candidate rows for White and Black; the selected-side version returns only candidates for the actual side to move. This is therefore an authorized output-scope change, not a behavior-preserving optimization under the initial constraints.

| Approach | Speed / engine work | Memory | Code and maintenance | Side effects |
|---|---|---|---|---|
| Run two full-strength searches sequentially (original) | Historical GUI sample: 6.445 s for both sides at the 2-second per-side setting; two side searches, with fallback queries if MultiPV lines are missing | One Stockfish process; more total engine work | 200 lines across the engine method and result UI functions | Full White and Black results |
| Run separate Stockfish instances concurrently (considered, not implemented) | Could approach one per-side wall-clock budget, but contention, launch/stop overhead, and response latency were not benchmarked | Two engine processes and increased CPU/memory | Requires process-pool, failure, timeout, and shutdown handling | Preserves both result panels; concurrent engine lifecycle adds failure modes |
| One deadline-bounded MultiPV search for the selected side (chosen per user direction) | 1.956 s at the same 2-second setting; one analysis call and up to three legal candidates | One existing engine process; less total engine work | 217 lines across the engine method and selected-side UI functions versus 200 before (+17); one search path and explicit deadline/side synchronization | Shows only the side to move, as now confirmed by the user |

These engine timings are recorded in `GUI_ENTRYPOINT_TIMINGS.csv`; they are observed runs, not a repeatable controlled benchmark. No profiling or benchmark was used to justify the correctness fixes for missing `time` and `re` imports.

### Quiet level-two practice hints: reuse event analysis

`review_practice_hint()` first analyzes move events to look for a positional hint at level two. If it finds none and no tactical shortcut applies, it analyzes the same transition again with reply-line details, then only checks for the same positional event kinds. The second call's response-specific events cannot change that filtered positional result.

| Approach | Speed evidence | Memory | Code and maintenance | Side effects |
|---|---|---|---|---|
| Keep both analyzer calls (original) | Controlled 50-call median: 3.589 ms; 100 `analyze_review_events` invocations | Event tuple is transient and the second result replaces it | 92 lines in `review_practice_hint`; clear stages, but repeats the expensive full event scan | Returns the observed generic hint for the quiet `h3` position |
| Reuse the first event tuple for the later positional lookup (chosen) | Same-process median: 1.862 ms; 50 analyzer invocations; 1.93× faster (48.1% lower latency) on the quiet `h3` path | No additional retained state; holds the first tuple until the final lookup | 94 lines (+2); an explicit `events is None` guard keeps the lower-level path's reply-aware analysis | Identical hint text; response-only event kinds are not considered by the positional lookup |
| Build a shared positional-event pass inside `analyze_review_events` and combine it with reply-specific events | Not implemented or separately benchmarked; it could remove repeated work while centralizing event policy | Similar transient tuple storage | More invasive coupling between hint needs and the general review-event analyzer | Requires broader equivalence tests for event ordering and all callers |

The benchmark used the same initial board, `h3` as the engine move and `d5` as the reply, five warm-up calls per version, then 50 timed calls. Both versions returned the same hint. A regression test asserts one analyzer call and that exact text. The earlier `test_review_analysis` run passed 89 tests; the full suite has since grown to 126 passing tests.

## Open issues and next steps

1. Exercise puzzle wrong-move/challenge scoring and Coach actions with an available review; the empty-state Retry guard is verified.
2. Test selected-game and stored-PGN deletion cancel branches when safe fixture entries are available. Do not confirm profile deletion without separate action-time authorization.
3. Test PGN missing/unwritable paths and less common notation; Chess.com archive/download/read-only flows, window resizing, rapid input, async cancellation, review cancellation/repeat, and close during restricted engine startup. Standard-ready UI shutdown returned exit code 0.

## PGN import behavior and fixes

- The user chose to reject zero-move/header-only PGNs and preserve the current game. Import now raises before changing the board/history; unit tests assert the same board object, FEN and history list remain intact. The live GUI displayed “This PGN contains no moves; the current game was kept.”
- The user chose to allow malformed movetext while flagging it inline. The importer now allows legal prefixes when python-chess reports SAN parse errors and scans first-game source text for unrecognized movetext tokens omitted by its lexer. It blocks non-movetext errors such as invalid FEN/variant metadata. The live GUI imported e4 e5 Nf3 from the `Kg9` fixture and showed “Imported 3 plies · invalid token 'Kg9'; loaded legal prefix only.”
- Import still does not start a Stockfish reply, preserving its existing action. The old lower turn label incorrectly remained “Your turn,” while the board hint implied a search was active. Import now labels the side to move, and `_sync_board()` distinguishes “Stockfish to move” from “Stockfish is thinking.” Both were verified in the live GUI.
- **Scanner alternatives:** The old UI had no malformed-token scan. For the required first-unrecognized-token behavior, these are the three implementation options considered:

  | Approach | Speed and memory evidence | Code length, readability, maintenance | Side effects |
  |---|---|---|---|
  | Build a list of recognized-token spans, then scan the gaps | 0.1283 ms per call; 29,926 B peak traced allocation on the same 78-ply PGN | More intermediate span/list objects and index bookkeeping; explicit but longer | Same first-token result in the benchmark fixture |
  | Remove recognized movetext tokens with `MOVETEXT_REGEX.sub` and scan remaining text (chosen) | 0.1134 ms; 9,422 B peak; 11.6% faster and 68.5% lower peak allocation; same scanner outputs | 15-line helper; compact use of python-chess's existing lexer regex, followed by a small residual-token scan | Can inherit lexer quirks; mitigated by tests for comments, annotations, variations, move numbers, result and invalid token |
  | Stream through token matches and inspect gaps without materializing the full span list | Expected lower peak storage than the span-list option; not benchmarked | More custom iteration/index logic and a second parser-like path to maintain; potential advantage on very large files | No separate equivalence/performance result, so not selected |

  The scanner is 15 lines; the importer is 33 lines versus 23 at HEAD (+10 lines for empty rejection, partial-parse handling, warning and turn-state updates). Eight unit tests cover empty preservation, both malformed-token paths, valid annotations/comments/variations, first-game scope, blocking FEN errors, and turn-hint state.
- **Invalid encoding feedback:** A live `.pgn` containing invalid UTF-8 bytes was rejected without changing the current game, but its dialog displayed Python's decoder exception. The handler now maps `UnicodeDecodeError` to “This file is not valid UTF-8 text.” and leaves other import errors unchanged. A focused regression test asserts the message and exact board/history preservation. This is a clarity fix, not a speed optimization; the exception branch grew from one line to three.
- The scanner is a defensive check around python-chess’s permissive lexer, not a complete independent PGN parser. Remaining risks include uncommon suffixes, malformed comments and multi-game edge cases; these are listed above.

## Codebase architecture and inspection map

The repository contains a Qt desktop front end, an asynchronous UCI engine worker, chess review and coaching algorithms, local profile storage, optional public Chess.com archive import, release update helpers, and an opt-in reporting dashboard. `main.py` assembles/re-exports legacy APIs; the focused `offline_chess` modules implement the subsystems. Top-level tools manage the updater/dashboard and `tests`/`test_*.py` cover behavior. Runtime dependencies are PyQt6 (widgets/signals), python-chess (rules/PGN/UCI/Polyglot), cryptography (dashboard event decryption), SQLite (profile), and the bundled Stockfish executable. Optional Qt multimedia/text-to-speech integrations degrade when unavailable.

| Module | Main responsibilities and inspected code paths |
|---|---|
| `main.py`, `offline_chess/config.py`, `offline_chess/build_config.py` | QApplication startup, compatibility re-exports, version/paths/report URL and build-time configuration |
| `offline_chess/engine_worker.py` | Engine process/thread lifecycle; game move, evaluation, review, puzzle and Position Lab requests; cancellation and result signals |
| `offline_chess/review_logic.py` | Score/accuracy conversion, move classification, tactical and positional event detection, practice hints, narrative/coaching, filters and moment ranking |
| `offline_chess/openings.py` | TSV/PGN opening catalog parsing, position identity/index, name/depth lookup and book-exit analysis |
| `offline_chess/chesscom_data.py` | Chess.com public URL/JSON validation and workers; profile SQLite schema/migrations, local PGN persistence, review metadata and metrics |
| `offline_chess/ui/main_window.py` | Main game state, actions, imports/exports, engine/review signal coordination, opening labels and top-level tabs |
| `offline_chess/ui/position_lab.py`, `offline_chess/ui/board.py` | Editable position and candidate/PV UI; board drawing/input, evaluation bar and opening explorer |
| `offline_chess/ui/review_dialogs.py`, `offline_chess/ui/charts.py` | Puzzle/practice/review dialogs and progress; accuracy, Elo, Chess.com, shape, quality and momentum charts |
| `offline_chess/ui/profile_dialog.py`, `offline_chess/ui/feedback_dialog.py`, `offline_chess/ui/theme.py`, `offline_chess/ui/common.py` | Profile views/filters/settings, feedback form, board styles and shared layout helpers |
| `offline_chess/telemetry.py`, `offline_chess/usage_database.py`, `offline_chess/local_dashboard.py`, `dashboard_server.py`, `setup_dashboard_login.py`, `generate_dashboard_keys.py` | Explicit report payloads/rate limits, encrypted repository aggregation, loopback-only authenticated dashboard, credential/key setup |
| `app_updater.py` | GitHub release check, streamed SHA-256-verified download and Windows replacement/rollback helper |
| `old.py`, `collect_licenses.py`, `tools/gui_entrypoint_audit.py` | Legacy bot entry point, packaging license inventory, and offscreen GUI timing/smoke harness |

This is a module-level responsibility map, not a claim that every line of every function received a separate unit test. Static inventory covers the full repository; the principal optimization/correctness paths above were traced through their callers and tests. Areas without a measured hotspot were left untouched, especially database migrations, account data handling, engine lifecycle, and authenticated/reporting flows where a “simplification” could alter persistence or security behavior. The GUI coverage matrix above is intentionally incomplete and identifies unverified interaction paths.
