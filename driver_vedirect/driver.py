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
        self._warned_pids: set[str] = set()
        self._reported_bad = 0
        self._merged: dict[str, str] = {}  # PID-less (history) blocks, merged by key
        self._live: set[str] = set()  # topics published with a value
        self._cleared: set[str] = set()  # of those, the ones last cleared with an empty payload
        self._last_bad_report = float("-inf")

    def poll(self, port) -> None:  # noqa: ANN001 - any object with read(size) -> bytes
        """Read one chunk from the port and publish whatever complete blocks it finishes."""
        for fields in self._parser.feed(port.read(4096)):
            self._handle(fields, getattr(port, "name", ""))
        self._report_bad_frames()

    def _report_bad_frames(self) -> None:
        """Rate-limited (once per minute) warning for blocks dropped on a bad checksum."""
        now = self._clock()
        dropped = self._parser.bad_frames - self._reported_bad
        if dropped and now - self._last_bad_report >= BAD_FRAME_LOG_INTERVAL_S:
            logger.warning("Dropped %d VE.Direct block(s) with a bad checksum", dropped)
            self._reported_bad += dropped
            self._last_bad_report = now

    def _handle(self, fields: dict[str, str], port_name: str = "") -> None:
        # The shunt alternates a live block (PID) and an H1..H18 history block (no PID); both
        # are folded together by key so one never overwrites the other.
        if "PID" not in fields:
            self._merged.update(fields)
            return
        pid = fields.get("PID", "")
        device = pids.lookup(pid)
        if device is None:
            if pid not in self._warned_pids:
                self._warned_pids.add(pid)
                logger.warning("Unknown VE.Direct PID %s with fields %s", pid, sorted(fields))
            return
        if device["entity"] == "battery":
            self._handle_battery({**self._merged, **fields}, device, port_name)
            return
        serial = fields.get("SER#", "").lower()
        if not serial or device["entity"] != "solar":
            return  # other entities arrive in later tickets
        if serial not in self._identified:
            self._identified.add(serial)
            self._publish("solar", serial, "driver", DRIVER_KEY)
            self._publish("solar", serial, "name", device["model"])
        for prop, value in mapping.map_solar(fields).items():
            self._publish("solar", serial, prop, value)

    def _handle_battery(self, fields: dict[str, str], device: dict, port_name: str) -> None:
        match = _INTERFACE_TAIL.search(port_name)
        device_id = fields.get("SER#", "").lower() or (
            match.group(1) if match else self._config.device.id
        )
        if device_id not in self._identified:
            self._identified.add(device_id)
            override = self._config.devices.get(device_id)
            self._publish("battery", device_id, "driver", DRIVER_KEY)
            self._publish(
                "battery", device_id, "name", (override and override.name) or device["model"]
            )
        for prop, value in mapping.map_battery(fields).items():
            topic = _topic("battery", device_id, prop)
            if value is not None:
                self._cleared.discard(topic)
                self._publish("battery", device_id, prop, value)
            elif topic in self._live and topic not in self._cleared:
                # No estimate / no probe any more: clear what we published earlier.
                self._cleared.add(topic)
                self._publisher.publish(topic, "")

    def _publish(self, entity: str, device_id: str, prop: str, value: object) -> None:
        topic = _topic(entity, device_id, prop)
        self._live.add(topic)
        self._publisher.publish(topic, json.dumps(value))


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
