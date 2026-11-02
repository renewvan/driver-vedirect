"""PID table lookup. The data lives in pids.json; adding a device is one line there."""

from __future__ import annotations

import json
from pathlib import Path

_TABLE_PATH = Path(__file__).resolve().parent / "pids.json"


def _load() -> list[tuple[int, int, dict]]:
    rows = []
    for key, info in json.loads(_TABLE_PATH.read_text()).items():
        low, _, high = key.partition("-")
        rows.append((int(low, 16), int(high or low, 16), info))
    return rows


_ROWS = _load()


def lookup(pid: str) -> dict | None:
    try:
        value = int(pid, 16)
    except ValueError:
        return None
    for low, high, info in _ROWS:
        if low <= value <= high:
            return info
    return None
