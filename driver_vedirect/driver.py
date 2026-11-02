"""Publish loop: VE.Direct bytes -> frames -> renewvan/<entity>/<id>/<property>.

Read-only: nothing is ever written to the device. Tests drive this module through `Driver.poll`
with a fake port, publisher and clock.
"""

from __future__ import annotations

import json
import logging
import os
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
        self._parsers: dict[str, FrameParser] = {}
        self._identified: set[str] = set()
        self._warned_pids: set[str] = set()
        self._reported_bad = 0
        self._closed_bad = 0
        self._last_bad_report = float("-inf")

    def poll(self, port, key: str = "") -> bool:  # noqa: ANN001 - any object with read(size)
        """Read one chunk from the port and publish whatever complete blocks it finishes.

        `key` names the port (its by-id path) so each keeps its own half-read frame. Returns
        True when the port delivered any bytes. A failing read raises OSError to the caller.
        """
        parser = self._parsers.setdefault(key, FrameParser())
        data = port.read(4096)
        for fields in parser.feed(data):
            self._handle(fields)
        self._report_bad_frames()
        return bool(data)

    def forget_port(self, key: str) -> None:
        """Drop a port's partial frame (it was closed); counters stay in the totals."""
        parser = self._parsers.pop(key, None)
        if parser is not None:
            self._closed_bad += parser.bad_frames

    def _report_bad_frames(self) -> None:
        """Rate-limited (once per minute) warning for blocks dropped on a bad checksum."""
        now = self._clock()
        total = self._closed_bad + sum(p.bad_frames for p in self._parsers.values())
        dropped = total - self._reported_bad
        if dropped and now - self._last_bad_report >= BAD_FRAME_LOG_INTERVAL_S:
            logger.warning("Dropped %d VE.Direct block(s) with a bad checksum", dropped)
            self._reported_bad += dropped
            self._last_bad_report = now

    def _handle(self, fields: dict[str, str]) -> None:
        pid = fields.get("PID", "")
        device = pids.lookup(pid)
        if device is None:
            if pid not in self._warned_pids:
                self._warned_pids.add(pid)
                logger.warning("Unknown VE.Direct PID %s with fields %s", pid, sorted(fields))
            return
        serial = fields.get("SER#", "").lower()
        if not serial or device["entity"] != "solar":
            return  # other entities and serial-less devices arrive in later tickets
        if serial not in self._identified:
            self._identified.add(serial)
            self._publish("solar", serial, "driver", DRIVER_KEY)
            self._publish("solar", serial, "name", device["model"])
        for prop, value in mapping.map_solar(fields).items():
            self._publish("solar", serial, prop, value)

    def _publish(self, entity: str, device_id: str, prop: str, value: object) -> None:
        self._publisher.publish(_topic(entity, device_id, prop), json.dumps(value))


def run(config: AppConfig) -> None:
    import serial  # imported here so tests need no serial stack

    from driver_vedirect.discovery import Supervisor, run_loop

    # Board serial is read once, here: Publisher republishes this same payload on every reconnect.
    info_payload = build_info_payload(env=os.environ, cpuinfo_path=Path("/proc/cpuinfo"))
    publisher = Publisher(config.mqtt, info_payload)
    publisher.connect()

    def open_port(path: str, baud: int):  # read-only use; timeout=0 keeps reads non-blocking
        return serial.Serial(path, baud, timeout=0)

    supervisor = Supervisor(Driver(publisher, config), config, time.monotonic, open_port)
    try:
        run_loop(supervisor)
    finally:
        publisher.disconnect()
