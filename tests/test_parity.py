"""Offline parity with driver-venus, through the script's command line (the only seam).

Each capture is compared with a Venus reference: the D-Bus-shaped fixture derived from the same
capture (`fixtures/venus/<name>.dbus.json`, hand-checked against the capture's last block) run
through driver-venus's mapping.py, committed as a topic dump so CI needs no driver-venus checkout.
When driver-venus is available the same comparison also runs against its live mapping and the
committed dump is checked for staleness.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "compare_venus.py"
FIXTURES = ROOT / "tests" / "fixtures"
CAPTURES = ["smartshunt", "smartsolar-mppt", "orion-xs", "blue-smart"]


def venus_repo() -> Path | None:
    for candidate in (os.environ.get("VENUS_REPO"), ROOT.parent / "driver-venus"):
        if candidate and (Path(candidate) / "driver_venus" / "mapping.py").exists():
            return Path(candidate)
    return None


def compare(*args: str | Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def against_dump(name: str, dump: Path | None = None) -> subprocess.CompletedProcess:
    return compare(
        FIXTURES / f"{name}.raw",
        "--venus-topics",
        dump or FIXTURES / "venus" / f"{name}.topics.json",
    )


@pytest.mark.parametrize("name", CAPTURES)
def test_capture_matches_venus_reference_cleanly(name):
    result = against_dump(name)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def tampered(tmp_path: Path, name: str, topic_suffix: str, value) -> Path:
    dump = json.loads((FIXTURES / "venus" / f"{name}.topics.json").read_text())
    (topic,) = [t for t in dump if t.endswith(topic_suffix)]
    dump[topic] = value
    out = tmp_path / "dump.json"
    out.write_text(json.dumps(dump))
    return out


def test_numeric_drift_beyond_rounding_fails_and_names_the_property(tmp_path):
    dump = tampered(tmp_path, "smartsolar-mppt", "/battery_voltage_v", 13.5)

    result = against_dump("smartsolar-mppt", dump)

    assert result.returncode == 1
    assert "battery_voltage_v" in result.stdout


def test_numeric_difference_within_rounding_passes(tmp_path):
    dump = tampered(tmp_path, "smartsolar-mppt", "/battery_voltage_v", 13.332)

    assert against_dump("smartsolar-mppt", dump).returncode == 0


def test_states_are_compared_by_name(tmp_path):
    dump = tampered(tmp_path, "smartsolar-mppt", "/charge_state", "float")

    result = against_dump("smartsolar-mppt", dump)

    assert result.returncode == 1
    assert "charge_state" in result.stdout and "bulk" in result.stdout and "float" in result.stdout


def test_deliberate_v1_gaps_are_reported_as_expected_not_failures():
    shunt = against_dump("smartshunt")
    solar = against_dump("smartsolar-mppt")
    blue = against_dump("blue-smart")

    assert shunt.returncode == solar.returncode == blue.returncode == 0
    for text, props in (
        (shunt.stdout, ("charge_state", "capacity_ah", "remaining_ah")),
        (solar.stdout, ("yield_history_wh",)),
        (blue.stdout, ("power_w",)),
    ):
        expected = text.split("Expected v1 gaps", 1)[1]
        for prop in props:
            assert prop in expected


def test_a_topic_venus_has_but_v1_neither_publishes_nor_lists_as_a_gap_is_surfaced(tmp_path):
    out = against_dump("orion-xs").stdout
    assert "Unlisted" not in out  # nothing unlisted for the Orion XS

    dump = json.loads((FIXTURES / "venus" / "orion-xs.topics.json").read_text())
    dump["renewvan/charger/291/time_to_go_s"] = 5
    path = tmp_path / "dump.json"
    path.write_text(json.dumps(dump))

    result = against_dump("orion-xs", path)

    assert result.returncode == 0  # shared topics only decide the result
    assert "Unlisted" in result.stdout and "time_to_go_s" in result.stdout


@pytest.mark.skipif(venus_repo() is None, reason="driver-venus checkout not available")
@pytest.mark.parametrize("name", CAPTURES)
def test_capture_matches_live_venus_mapping_and_committed_dump_is_current(name, tmp_path):
    repo = venus_repo()
    fixture = FIXTURES / "venus" / f"{name}.dbus.json"

    result = compare(FIXTURES / f"{name}.raw", "--venus-fixture", fixture, "--venus-repo", repo)
    assert result.returncode == 0, result.stdout + result.stderr

    fresh = tmp_path / "fresh.json"
    dumped = compare("--venus-fixture", fixture, "--venus-repo", repo, "--dump-venus", fresh)
    assert dumped.returncode == 0, dumped.stderr
    committed = json.loads((FIXTURES / "venus" / f"{name}.topics.json").read_text())
    assert json.loads(fresh.read_text()) == committed
