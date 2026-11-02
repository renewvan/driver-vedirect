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


def classify(fields: dict[str, str]) -> dict | None:
    """Classify a device with an unknown PID by its fields; None when it is ambiguous.

    Rules apply in order: PV fields -> solar; DC_IN_V -> alternator charger; BMV, or SOC with CE
    -> battery; V + I + CS with none of the above -> AC charger.
    """
    if "VPV" in fields or "PPV" in fields:
        return {"entity": "solar", "model": "Victron solar"}
    if "DC_IN_V" in fields:
        return {"entity": "charger", "source": "alternator", "model": "Victron charger"}
    if "BMV" in fields or ("SOC" in fields and "CE" in fields):
        return {"entity": "battery", "model": "Victron battery monitor"}
    if {"V", "I", "CS"} <= fields.keys():
        return {"entity": "charger", "source": "ac", "model": "Victron charger"}
    return None
