"""Start the localhost Offline Chess usage dashboard."""
from __future__ import annotations

import os
from pathlib import Path


def _load_local_environment() -> None:
    env_file = Path(__file__).with_name(".env")
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    _load_local_environment()
    from offline_chess.local_dashboard import serve_local_dashboard
    serve_local_dashboard()
