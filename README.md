# Offline Chess

A local chess analysis app built with PyQt6, `python-chess`, and Stockfish. Games and profile data stay on this computer unless you choose to fetch public game archives from Chess.com.

## Run the app

Requirements: Python 3.10 or newer, PyQt6, `python-chess`, and a Stockfish 19 compatible engine.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install PyQt6 python-chess
python main.py
```

The app looks for `stockfish.exe` beside `main.py`. Set the `STOCKFISH_PATH` environment variable if the engine is installed elsewhere.

## Windows EXE and updates

Tagged releases build a single `OfflineChess.exe` that includes Stockfish and the Lichess opening database. The app checks the public GitHub Releases page in the background when it starts. If a newer release is available, it offers to download and install it, verifies the published SHA-256 checksum, then restarts with the new version. Run the EXE from a folder your Windows account can write to, such as Downloads or a folder under your user profile.

To publish a release, update `APP_VERSION` in `main.py`, commit the change, create the matching tag (for example `v0.10.0`), and push the tag. GitHub Actions builds and publishes the EXE and checksum. The release tag must match `APP_VERSION`.

The one-file build extracts its engine and opening database into a temporary folder while it runs. Profiles and downloaded games remain in the user's local application data folder, outside the EXE.

Opening names use the [Lichess chess-openings](https://github.com/lichess-org/chess-openings) TSV files in `openings/` (`a.tsv` through `e.tsv`). The profile shows the opening name and its ECO code together, for example `Bird Opening · A02`.

## Board appearance

Choose the board and piece styles in **Profile → Overview**. Board themes include Classic Green, Walnut, Ocean Blue, Soft Violet, Slate, Mint, Crimson, Graphite, Desert, Nordic, Rose, High Contrast, and Monochrome. Piece styles include Tournament, Modern, Letters, Badge, and Bold. The selected styles are saved locally and apply to play, review, and opening boards.

## Chess.com game review

Open **Profile → Chess.com**, enter a Chess.com username, then choose **Find archives**. Select a month to browse its games and download selected games or the full month, or use **Download all months** to fetch every available archive. Only public archives are available through Chess.com's read-only Published-Data API; its archive can lag behind live games. This app is independent and is not affiliated with Chess.com.

Downloaded games are stored as individual PGN files under:

```text
%LOCALAPPDATA%\OfflineChess\chesscom_games\<username>\<YYYY-MM>\
```

Use **Open PGN folder** to browse them. Opening names use Chess.com's ECO URL when present (including the `ECOUrl` PGN header as a fallback), with the bundled Lichess opening catalog used to normalize names. The dashboard keeps Chess.com's optional per-game accuracy values separate from local Stockfish review accuracy; games without a Chess.com accuracy value are excluded from that metric. Re-download older games to populate the value when Chess.com has calculated it. Opening mastery estimates how many of your moves remain in the loaded Lichess opening lines before your first departure. The table lists all detected openings by color for the selected period, with per-line book depth and sample size; select a row to chart monthly results and local opening-phase review accuracy, or double-click to filter its games. Chess.com's own book can differ. Tactical review examples can also be double-clicked to run a fresh review and jump to the position. Tactics summarize found forks, pins, checkmates, and opponent pieces newly left hanging; missed fork, pin, and mate chances; and pieces either player leaves hanging or checkmates allowed in current local reviews. Game-flow patterns include each pattern's share of reviewed games with a usable evaluation trace and a selectable average user-side expected-score trace. Move quality over time shows monthly proportions for major quality bands across reviewed moves. The Chess.com profile combines a game library with an Insights-style dashboard: score and win rate, monthly results and both accuracy trends, rating history, performance by time control, color, opponent rating and self-reported opponent country, termination, day and local time, opening results by color with opening-phase accuracy, review coverage, accuracy by game phase and move number, piece accuracy, castling results, move classifications, tactical patterns, and recurring lessons. Select **Lookup opponent countries** to fetch public player-profile country data for opponents; lookups run serially, are cached locally, and can be stopped. Recent period filters also compare score and both accuracy measures with the preceding period. Rating expectation uses an Elo formula as a model baseline, not a similar-player benchmark. Game-flow patterns are local estimates from engine review traces, not Chess.com labels. The period and time-control selectors scope the dashboard and rating history. A result-based performance Elo estimate appears when at least eight rated results have opponent ratings; it is not an official Chess.com rating. Review metrics use the current analysis version, so saved reviews must be refreshed after analysis changes before their measures appear in current profile comparisons.

## Tests

Run the review and profile test suite from PowerShell:

```powershell
python -m unittest test_review_analysis -q
```
