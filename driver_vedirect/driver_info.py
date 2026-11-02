"""Driver info: the retained payload published on renewvan/<driver>/info.

Copied per repo on purpose (no shared package; see renewvan/hub#35). Driver
info is the static manifest.json plus the build-stamped version and the
Board serial.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

# Image builds copy manifest.json beside this module; a checkout run falls
# back to the repo-root file.
_MANIFEST_PATHS = (
    Path(__file__).resolve().parent / "manifest.json",
    Path(__file__).resolve().parent.parent / "manifest.json",
)


def _load_manifest() -> dict:
    for path in _MANIFEST_PATHS:
        if path.is_file():
            return json.loads(path.read_text())
    raise FileNotFoundError(f"manifest.json not found in {[str(p) for p in _MANIFEST_PATHS]}")


def _read_board_serial(cpuinfo_path: Path) -> str | None:
    """`Serial` line of /proc/cpuinfo; None when unreadable, empty or all zeros."""
    try:
        text = cpuinfo_path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "Serial":
            serial = value.strip()
            return serial if serial.strip("0") else None
    return None


def build_info_payload(env: Mapping[str, str], cpuinfo_path: Path) -> str:
    info = _load_manifest()
    info["version"] = env.get("DRIVER_VERSION") or "dev"
    board_serial = _read_board_serial(cpuinfo_path)
    if board_serial is not None:
        info["board_serial"] = board_serial
    return json.dumps(info)
