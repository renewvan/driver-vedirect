#!/usr/bin/env python3
"""Offline parity check: driver-vedirect against driver-venus on the same device, no hardware.

    compare_venus.py CAPTURE.raw --venus-fixture F.dbus.json [--venus-repo ../driver-venus]
    compare_venus.py CAPTURE.raw --venus-topics F.topics.json
    compare_venus.py --venus-fixture F.dbus.json --dump-venus F.topics.json   # regenerate a dump

The vedirect side replays the capture's bytes through the Driver (fake port, publisher, clock).
The Venus side runs driver-venus's `mapping.py` over a D-Bus-shaped fixture (the same
`{"N/<portal>/<service>/<instance>/<path>": {"value": ...}}` shape a Venus capture has) or reads a
topic dump of that run. Only shared topics decide the result: numerics must agree within the
rounding of their shared unit and states by name. Venus instance ids (device numbers) and vedirect
instance ids (serials) never agree, so topics are paired by (entity, property); each side must
hold exactly one instance per entity. Identity topics (`name`, `driver`) are not compared.

Exit status: 0 clean, 1 on any mismatch, 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORTAL = "PORTAL"
IDENTITY = {"name", "driver"}

# Deliberate v1 gaps (hub#124): one-sided topics that are expected, not failures. Charger
# `power_w` is a gap only where one side lacks it (derived V x I for the Blue Smart); when both
# publish it (Orion XS with P) it is compared like any other topic.
EXPECTED_GAPS: dict[tuple[str, str], str] = {
    ("battery", "charge_state"): "a shunt has no stage; Venus fans out the system state",
    ("battery", "capacity_ah"): "history and fuel gauge come from the database",
    ("battery", "remaining_ah"): "history and fuel gauge come from the database",
    ("solar", "yield_history_wh"): "history comes from the database",
    ("charger", "power_w"): "derived as V x I for the Blue Smart / Orion XS without P",
}

# Absolute tolerance per property: the rounding of the coarser shared unit (VE.Direct text
# resolution, Venus float32 noise); anything not listed must match to 1e-6.
TOLERANCE: dict[str, float] = {
    "voltage_v": 0.005,
    "battery_voltage_v": 0.005,
    "pv_voltage_v": 0.005,
    "input_voltage_v": 0.011,  # DC_IN_V is 0.01 V
    "current_a": 0.005,
    "battery_current_a": 0.005,
    "pv_current_a": 0.005,
    "soc_pct": 0.1,  # SOC is per mille
    "power_w": 1.0,  # whole watts
    "yield_today_wh": 10.0,  # H20 is 0.01 kWh
    "temperature_c": 1.0,
    "time_to_go_s": 60.0,  # TTG is minutes
}


class _FakePort:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int = 4096) -> bytes:
        out, self._data = self._data[:size], self._data[size:]
        return out

    def pending(self) -> bool:
        return bool(self._data)


class _FakePublisher:
    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic: str, payload: str) -> None:
        self.retained[topic] = payload


def vedirect_topics(raw: bytes) -> dict[str, object]:
    """Replay a capture through the Driver; the retained topics left standing, decoded."""
    from driver_vedirect.config import AppConfig, DeviceConfig, MqttConfig
    from driver_vedirect.driver import Driver

    mqtt = MqttConfig("localhost", 1883, None, None, "parity", 1.0, 30.0)
    config = AppConfig(mqtt=mqtt, device=DeviceConfig(id="vedirect", update_interval_ms=3000))
    publisher, port = _FakePublisher(), _FakePort(raw)
    driver = Driver(publisher, config, clock=lambda: 1000.0)  # frozen: nothing goes stale
    while port.pending():
        driver.poll(port)
    return {t: json.loads(p) for t, p in publisher.retained.items() if p != ""}


def venus_topics(fixture: dict, venus_repo: Path) -> dict[str, object]:
    """Run driver-venus's mapping over a D-Bus-shaped fixture; cleared (null) topics are dropped."""
    sys.path.insert(0, str(venus_repo))
    try:
        from driver_venus import mapping as vm
    finally:
        sys.path.remove(str(venus_repo))

    pairs: list[tuple[str, object]] = []
    history, fuel, bms = vm.SolarYieldHistory(), vm.BatteryFuelGauge(), vm.BatteryBms()
    battery_ids: list[str] = []
    system_state = None
    for topic, payload in fixture.items():
        if topic == f"N/{PORTAL}/system/0/Batteries":
            mapped = vm.map_batteries(payload)
            pairs += mapped
            battery_ids += sorted({t.split("/")[2] for t, _ in mapped} - set(battery_ids))
        elif topic == f"N/{PORTAL}/system/0/SystemState/State":
            system_state = payload
        elif (parts := vm.split_service_topic(topic, PORTAL)) is not None:
            pairs += vm.map_service_message(*parts, payload)
            history.handle(*parts, payload)
            pairs += fuel.handle(*parts, payload)
            bms.handle(*parts, payload)
    if system_state is not None:
        pairs += vm.map_charge_state(system_state, battery_ids)
    pairs += history.flush() + bms.flush()
    return {t: v for t, v in pairs if v is not None}


def by_entity_property(topics: dict[str, object], side: str) -> dict[tuple[str, str], object]:
    out: dict[tuple[str, str], object] = {}
    ids: dict[str, set[str]] = {}
    for topic, value in topics.items():
        _, entity, instance, prop = topic.split("/", 3)
        ids.setdefault(entity, set()).add(instance)
        if prop not in IDENTITY:
            out[(entity, prop)] = value
    for entity, found in ids.items():
        if len(found) > 1:
            raise ValueError(f"{side} has several {entity} instances {sorted(found)}; need one")
    return out


def _equal(prop: str, a: object, b: object) -> bool:
    numeric = (int, float)
    if isinstance(a, numeric) and isinstance(b, numeric) and not isinstance(a, bool):
        return abs(a - b) <= TOLERANCE.get(prop, 1e-6)
    return a == b


def compare(ved: dict[str, object], ven: dict[str, object]) -> tuple[list[str], list[str]]:
    """Returns (report lines, failures)."""
    mine, theirs = by_entity_property(ved, "vedirect"), by_entity_property(ven, "venus")
    lines: list[str] = []
    failures: list[str] = []
    shared = sorted(mine.keys() & theirs.keys())
    lines.append(f"Compared {len(shared)} shared topics")
    for key in shared:
        a, b = mine[key], theirs[key]
        if _equal(key[1], a, b):
            lines.append(f"  ok       {key[0]}/{key[1]}: vedirect {a!r}  venus {b!r}")
        else:
            failures.append(f"{key[0]}/{key[1]}: vedirect {a!r} != venus {b!r}")
    expected, unlisted = [], []
    for side, only in (
        ("vedirect only", mine.keys() - theirs.keys()),
        ("venus only", theirs.keys() - mine.keys()),
    ):
        for key in sorted(only):
            value = (mine if side == "vedirect only" else theirs)[key]
            row = f"  {side:<13} {key[0]}/{key[1]} = {value!r}"
            if key in EXPECTED_GAPS:
                expected.append(f"{row}  ({EXPECTED_GAPS[key]})")
            else:
                unlisted.append(row)
    if expected:
        lines.append("Expected v1 gaps (not failures)")
        lines += expected
    if unlisted:
        lines.append("Unlisted one-sided topics (not shared, so not failures; review)")
        lines += unlisted
    return lines, failures


def default_venus_repo() -> Path:
    return Path(os.environ.get("VENUS_REPO") or ROOT.parent / "driver-venus")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("capture", nargs="?", type=Path, help="raw VE.Direct capture")
    ap.add_argument("--venus-fixture", type=Path, help="D-Bus-shaped Venus fixture (.dbus.json)")
    ap.add_argument("--venus-topics", type=Path, help="Venus topic dump instead of a fixture")
    ap.add_argument("--venus-repo", type=Path, default=None, help="driver-venus checkout")
    ap.add_argument("--dump-venus", type=Path, help="write the Venus topics of the fixture here")
    args = ap.parse_args(argv)

    try:
        if args.venus_fixture:
            repo = args.venus_repo or default_venus_repo()
            if not (repo / "driver_venus" / "mapping.py").exists():
                print(
                    f"driver-venus not found at {repo} (use --venus-repo or VENUS_REPO)",
                    file=sys.stderr,
                )
                return 2
            venus = venus_topics(json.loads(args.venus_fixture.read_text()), repo)
        elif args.venus_topics:
            venus = json.loads(args.venus_topics.read_text())
        else:
            ap.error("give --venus-fixture or --venus-topics")
        if args.dump_venus:
            args.dump_venus.write_text(json.dumps(venus, indent=1, sort_keys=True) + "\n")
            if not args.capture:
                return 0
        if not args.capture:
            ap.error("a capture is required unless only dumping")
        lines, failures = compare(vedirect_topics(args.capture.read_bytes()), venus)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if failures:
        print("Mismatches")
        print("\n".join(f"  FAIL     {f}" for f in failures))
        print("FAIL")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
