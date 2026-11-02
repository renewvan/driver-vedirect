"""Publish loop: VE.Direct bytes -> frames -> renewvan/<entity>/<id>/<property>.

Read-only: nothing is ever written to the device. Tests drive this module through `Driver.poll`
with a fake port, publisher and clock.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from driver_vedirect import mapping, pids
from driver_vedirect.config import AppConfig
from driver_vedirect.driver_info import build_info_payload
from driver_vedirect.frames import FrameParser
from driver_vedirect.publisher import Publisher

logger = logging.getLogger(__name__)

# This Driver's key: its repo name minus `driver-`, equal to manifest.json's `driver` and to the
# `renewvan/<key>/info` segment. Entity instances name it on `renewvan/vedirect/<id>/driver`.
DRIVER_KEY = "vedirect"
BAD_FRAME_LOG_INTERVAL_S = 60.0
# Silence is an invalid reading (hub ADR 0008): see docs/adr/0001-clear-live-values-on-silence.md.
STALE_AFTER_S = 10.0


# `...-if02-port0` -> `if02`: the adapter port a serial-less device (the SmartShunt) is known by.
_INTERFACE_TAIL = re.compile(r"-(if\d+)-port\d+$")


def _topic(entity: str, device_id: str, prop: str) -> str:
    return f"renewvan/{entity}/{device_id}/{prop}"


class Driver:
    """Bytes from a serial port in, retained entity topics out. `clock` is injected for tests."""

    def __init__(
        self, publisher: Publisher, config: AppConfig, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._publisher = publisher
        self._config = config
        self._clock = clock
        self._parser = FrameParser()
        self._identified: set[str] = set()
        # (entity, id) -> [last valid frame time, live properties currently published]
        self._seen: dict[tuple[str, str], tuple[float, set[str]]] = {}
        self._warned_pids: set[str] = set()
        self._warned_ports: set[int] = set()
        self._noted_unverified: set[str] = set()
        self._last_err: dict[str, str] = {}
        self._merged: dict[str, str] = {}  # PID-less (shunt history) blocks, merged by key
        self._reported_bad = 0
        self._last_bad_report = float("-inf")

    def poll(self, port) -> None:  # noqa: ANN001 - any object with read(size) -> bytes
        """Read one chunk from the port and publish whatever complete blocks it finishes."""
        for fields in self._parser.feed(port.read(4096)):
            self._handle(fields, port)
        self._clear_stale()
        self._report_bad_frames()

    def _clear_stale(self) -> None:
        """Entity-generic: empty retained payloads for the live props of a silent device."""
        now = self._clock()
        for (entity, device_id), (last, live) in self._seen.items():
            if live and now - last >= STALE_AFTER_S:
                for prop in sorted(live):
                    self._publisher.publish(_topic(entity, device_id, prop), "")
                live.clear()

    def _report_bad_frames(self) -> None:
        """Rate-limited (once per minute) warning for blocks dropped on a bad checksum."""
        now = self._clock()
        dropped = self._parser.bad_frames - self._reported_bad
        if dropped and now - self._last_bad_report >= BAD_FRAME_LOG_INTERVAL_S:
            logger.warning("Dropped %d VE.Direct block(s) with a bad checksum", dropped)
            self._reported_bad += dropped
            self._last_bad_report = now

    def _handle(self, fields: dict[str, str], port) -> None:  # noqa: ANN001
        # The shunt alternates a live block (PID) and an H1..H18 history block (no PID); the
        # history is folded in by key so neither block overwrites the other.
        if "PID" not in fields:
            self._merged.update(fields)
            return
        pid = fields.get("PID", "")
        device = pids.lookup(pid)
        if device is None:
            device = pids.classify(fields)
        if device is None:
            if id(port) not in self._warned_ports:
                self._warned_ports.add(id(port))
                logger.warning(
                    "Unrecognized VE.Direct device (PID %s) with fields %s; publishing nothing",
                    pid,
                    sorted(fields),
                )
            return
        if device.get("verified") is False and pid not in self._noted_unverified:
            self._noted_unverified.add(pid)
            logger.info("First sight of unverified VE.Direct PID %s (%s)", pid, device["model"])
        entity = device["entity"]
        if entity == "battery":
            fields = {**self._merged, **fields}
        serial = fields.get("SER#", "").lower()
        if not serial and entity == "battery":  # the SmartShunt: its adapter port names it
            match = _INTERFACE_TAIL.search(getattr(port, "name", ""))
            serial = match.group(1) if match else self._config.device.id
        if not serial or entity not in ("solar", "charger", "battery"):
            return  # serial-less devices other than the shunt are not published
        if entity == "charger":
            self._log_err_change(serial, fields)
        if serial not in self._identified:
            self._identified.add(serial)
            self._publish(entity, serial, "driver", DRIVER_KEY)
            name = self._config.device_names.get(serial, device["model"])
            self._publish(entity, serial, "name", name)
            if "source" in device:
                self._publish(entity, serial, "source", device["source"])
        mapper = {
            "charger": mapping.map_charger,
            "battery": mapping.map_battery,
        }.get(entity, mapping.map_solar)
        live = self._seen.setdefault((entity, serial), (0.0, set()))[1]
        self._seen[(entity, serial)] = (self._clock(), live)
        for prop, value in mapper(fields).items():
            if value is None:  # no estimate / no probe (TTG -1, T ---): clear an earlier value
                if prop in live:
                    self._publisher.publish(_topic(entity, serial, prop), "")
                    live.discard(prop)
                continue
            self._publish(entity, serial, prop, value)
            live.add(prop)

    def _log_err_change(self, serial: str, fields: dict[str, str]) -> None:
        err = fields.get("ERR")
        if err is None:
            return
        previous = self._last_err.get(serial, "0")  # a healthy first reading is not news
        self._last_err[serial] = err
        if err != previous:
            logger.warning("Charger %s ERR changed %s -> %s", serial, previous, err)

    def _publish(self, entity: str, device_id: str, prop: str, value: object) -> None:
        self._publisher.publish(_topic(entity, device_id, prop), json.dumps(value))


def run(config: AppConfig) -> None:
    import serial  # imported here so tests need no serial stack

    # Board serial is read once, here: Publisher republishes this same payload on every reconnect.
    info_payload = build_info_payload(env=os.environ, cpuinfo_path=Path("/proc/cpuinfo"))
    publisher = Publisher(config.mqtt, info_payload)
    publisher.connect()
    # Read-only: the port is only ever read from. Port discovery and reconnects are a later ticket.
    port = serial.Serial(config.device.port, config.device.baud, timeout=1)
    driver = Driver(publisher, config)
    try:
        while True:
            driver.poll(port)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        port.close()
        publisher.disconnect()
