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
# Silence is an invalid reading (hub ADR 0008): see docs/adr/0001-clear-live-values-on-silence.md.
STALE_AFTER_S = 10.0


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
        self._reported_bad = 0
        self._last_bad_report = float("-inf")

    def poll(self, port) -> None:  # noqa: ANN001 - any object with read(size) -> bytes
        """Read one chunk from the port and publish whatever complete blocks it finishes."""
        for fields in self._parser.feed(port.read(4096)):
            self._handle(fields)
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
        live = self._seen.setdefault(("solar", serial), (0.0, set()))[1]
        self._seen[("solar", serial)] = (self._clock(), live)
        for prop, value in mapping.map_solar(fields).items():
            self._publish("solar", serial, prop, value)
            live.add(prop)

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
