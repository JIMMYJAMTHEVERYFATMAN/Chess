"""Copy license and notice files from the isolated release Python environment."""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import re
import shutil
import sys
from pathlib import Path


LICENSE_HINTS = ("license", "copying", "notice", "copyright")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    copied = 0
    distributions = sorted(importlib.metadata.distributions(), key=lambda item: (
        (item.metadata.get("Name") or "unknown").lower(), item.version
    ))
    with (output / "THIRD_PARTY_COMPONENTS.csv").open("w", newline="", encoding="utf-8") as manifest:
        writer = csv.writer(manifest)
        writer.writerow(("name", "version", "declared_license"))
        for distribution in distributions:
            writer.writerow((distribution.metadata.get("Name", "unknown"), distribution.version,
                             distribution.metadata.get("License-Expression") or
                             distribution.metadata.get("License", "Unspecified; inspect packaged license files")))

    for distribution in distributions:
        name = distribution.metadata.get("Name", "unknown")
        version = distribution.version
        package_dir = output / f"{safe_name(name)}-{safe_name(version)}"
        for item in distribution.files or ():
            relative = Path(str(item))
            parts_lower = [part.lower() for part in relative.parts]
            filename_lower = relative.name.lower()
            if not (any("license" in part for part in parts_lower) or
                    any(filename_lower.startswith(hint) for hint in LICENSE_HINTS)):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            destination = package_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1

    # CPython's license is outside site-packages, but the interpreter is bundled.
    for filename in ("LICENSE.txt", "LICENSE"):
        source = Path(sys.base_prefix) / filename
        if source.is_file():
            shutil.copy2(source, output / f"Python-{filename}")
            copied += 1
            break
    if not copied:
        raise SystemExit("No third-party license files were found in the build environment.")


if __name__ == "__main__":
    main()
