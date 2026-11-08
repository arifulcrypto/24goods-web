#!/usr/bin/env python3
"""Run outside the API with read access to backup markers only; publish safe recovery facts.

No Docker socket, commands, network, secret values or vault keys. Mount only the output
DIRECTORY read-only into the API at /run/24goods-operations (atomic replaces stay visible).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


def collect(root: Path) -> dict:
    sets = sorted(
        p
        for p in root.iterdir()
        if STAMP.fullmatch(p.name) and p.is_dir() and (p / "COMPLETE").is_file()
    )
    result = {
        "last_complete_at": None,
        "offsite_verified": None,
        "last_restore_test_at": None,
    }
    if sets:
        latest = sets[-1]
        text = (latest / "metadata.txt").read_text()[:4096]
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        value = fields.get("created_at", "")
        if re.fullmatch(r"[0-9TZ:-]{20}", value):
            result["last_complete_at"] = value
        result["offsite_verified"] = (latest / "OFFSITE_VERIFIED").is_file()
        # Operator writes this only after an isolated restore drill, never from backup success.
        marker = root / "LAST_RESTORE_TEST"
        if marker.is_file():
            value = marker.read_text()[:128].strip()
            if re.fullmatch(r"[0-9TZ:-]{20}", value):
                result["last_restore_test_at"] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = collect(args.backups)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, name = tempfile.mkstemp(dir=args.output.parent, prefix=".status-")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
        os.replace(name, args.output)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
