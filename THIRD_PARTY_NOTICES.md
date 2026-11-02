# Third-party software and data notices

Offline Chess includes or uses the following third-party components. Their names and marks remain the property of their respective owners. Offline Chess is an independent project and is not affiliated with Chess.com, Lichess, Stockfish, Riverbank Computing, or The Qt Company.

## Application source

The Offline Chess application source in this repository is © 2026 Offline Chess contributors and is licensed under **GNU GPL version 3 only**. See [`LICENSE`](LICENSE). This GPL-compatible choice is required by the PyQt6 GPL distribution and is compatible with the GPL-3.0-or-later `python-chess` library. It does not transfer ownership of third-party work.

## Bundled runtime components

| Component | Version used by the Windows release workflow | License and notices |
|---|---:|---|
| PyQt6 Python bindings | 6.9.1 | GNU GPL v3. The included GPL text is in [`licenses/GPL-3.0.txt`](licenses/GPL-3.0.txt). The corresponding upstream source distribution is available from [PyPI](https://pypi.org/project/PyQt6/6.9.1/#files). PyQt is also offered commercially by Riverbank Computing. |
| Qt libraries included in the PyQt6 wheel | 6.9.1 | GNU LGPL v3 for the Qt libraries selected by the GPL PyQt6 wheel, subject to module-specific exceptions. See [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt), [Qt licensing](https://doc.qt.io/qt-6/licensing.html), and [Qt 6.9.1 source](https://download.qt.io/archive/qt/6.9/6.9.1/single/). |
| `python-chess` compatibility metapackage and `chess` library | 1.999 and 1.11.2, respectively | GNU GPL version 3 or later. The app imports the `chess` library; the metapackage pins the project-compatible dependency. [Upstream project and license](https://github.com/niklasf/python-chess), [PyPI `chess` 1.11.2 metadata](https://pypi.org/project/chess/1.11.2/). |
| Stockfish chess engine | 19 (`sf_19`) | GNU GPL v3. The release includes the matching source archive, the Stockfish `Copying.txt` and `AUTHORS` inside that archive, and the `nn-1a298aa575a0.nnue` network used by the binary. See [Stockfish source](https://github.com/official-stockfish/Stockfish/tree/sf_19), [Stockfish license information](https://github.com/official-stockfish/Stockfish), and [Stockfish networks](https://github.com/official-stockfish/networks). |
| Python runtime and standard library | Python 3.12 | Python Software Foundation License 2.0. [Python license](https://docs.python.org/3/license.html). |
| PyInstaller bootloader | 6.22.3 | GPL with the PyInstaller bootloader exception. [PyInstaller license](https://pyinstaller.org/en/stable/license.html). |
| `cryptography` | 44.x–47.x accepted by the build | Apache License 2.0 or BSD 3-Clause. [Upstream project and license files](https://github.com/pyca/cryptography). Used by the localhost dashboard to decrypt AES-256-GCM event records. |
| PyQt6-sip support module | Resolved from the PyQt6 6.9.1 dependency | BSD 2-Clause. See [`licenses/BSD-2-Clause.txt`](licenses/BSD-2-Clause.txt) and [upstream SIP project](https://github.com/Python-SIP/sip). |

The Stockfish NNUE network is published by the Stockfish project. Stockfish documents its NNUE upload process as CC0; the neural-network training datasets it uses include Leela Chess Zero data under ODbL. See [Stockfish's NNUE guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html) and [Stockfish acknowledgements](https://github.com/official-stockfish/Stockfish). The app does not redistribute Leela's training database.

## Opening-name data

The files `openings/a.tsv` through `openings/e.tsv` are derived from the [Lichess chess-openings dataset](https://github.com/lichess-org/chess-openings), maintained by Lichess contributors. The dataset README states that the collection is in the public domain and, insofar as copyright applies, is released under the **Creative Commons CC0 1.0 Universal Public Domain Dedication**. This attribution is provided in recognition of Lichess's work; CC0 does not require attribution. Offline Chess is not affiliated with Lichess.

## Other bundled material

The app's board themes, piece drawings, and interface code are project code rather than copied third-party image packs. No Lichess branding or Chess.com logos are bundled.

## Distribution

The Windows release attaches a license-notices archive and the corresponding Stockfish source and network files. The GitHub release's source-code archive contains the corresponding Offline Chess source at the release tag, including the PyInstaller build configuration and workflow. For Qt's LGPL components, the included Qt license and upstream source information are provided so recipients can inspect and replace the LGPL libraries in accordance with their license terms.

This inventory covers the currently declared direct dependencies and bundled assets; the release also includes license files discovered in the Python build environment. It is not a complete legal audit, legal advice, or a guarantee against legal claims. Verify the exact contents and terms of every release artifact and obtain qualified legal advice for distribution decisions. Verify the precise terms of any future components added to a release.
