"""By-id port discovery and self-healing reconnect, shared by the container and systemd installs.

The Supervisor scans the by-id directory against `port_globs`, opens every match, polls all open
ports each step and, when a port fails to open or read, closes it and retries after a doubling
backoff. Devices are identified by what they report (see Driver), never by which port they use.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

from driver_vedirect.config import AppConfig
from driver_vedirect.driver import Driver

logger = logging.getLogger(__name__)

IDLE_SLEEP_S = 0.2


@dataclass
class _Slot:
    port: object | None = None
    next_try: float = float("-inf")
    delay: float = 0.0


class Supervisor:
    """`opener(path, baud)` returns a non-blocking port; `list_dir(path)` lists by-id names."""

    def __init__(
        self,
        driver: Driver,
        config: AppConfig,
        clock: Callable[[], float],
        opener: Callable[[str, int], object],
        list_dir: Callable[[str], list[str]] = os.listdir,
    ) -> None:
        self._driver = driver
        self._config = config
        self._clock = clock
        self._opener = opener
        self._list_dir = list_dir
        self._slots: dict[str, _Slot] = {}

    def step(self) -> bool:
        """Scan, (re)open and poll every port once. True when any port delivered bytes."""
        wanted = set(self._scan())
        for path in list(self._slots):
            if path not in wanted:
                self._release(path)
                del self._slots[path]
        got_data = False
        for path in sorted(wanted):
            slot = self._slots.setdefault(path, _Slot())
            if slot.port is None and not self._open(path, slot):
                continue
            got_data |= self._poll(path, slot)
        return got_data

    def close(self) -> None:
        for path in list(self._slots):
            self._release(path)

    def _scan(self) -> list[str]:
        directory = self._config.serial.by_id_dir
        try:
            names = self._list_dir(directory)
        except OSError:
            return []  # no adapter has ever been plugged in
        globs = self._config.serial.port_globs
        return [
            os.path.join(directory, n)
            for n in names
            if any(fnmatch.fnmatchcase(n, g) for g in globs)
        ]

    def _open(self, path: str, slot: _Slot) -> bool:
        if self._clock() < slot.next_try:
            return False
        try:
            slot.port = self._opener(path, self._config.device.baud)
        except OSError as exc:
            self._fail(path, slot, f"open failed: {exc}")
            return False
        logger.info("Opened %s", path)
        return True

    def _poll(self, path: str, slot: _Slot) -> bool:
        try:
            received = self._driver.poll(slot.port, key=path)
        except OSError as exc:
            self._release(path)
            self._fail(path, slot, f"read failed: {exc}")
            return False
        if received:
            slot.delay = 0.0  # a healthy port earns a fresh, short backoff next time
        return received

    def _fail(self, path: str, slot: _Slot, why: str) -> None:
        serial = self._config.serial
        slot.delay = min(slot.delay * 2, serial.backoff_max_s) or serial.backoff_min_s
        slot.next_try = self._clock() + slot.delay
        logger.warning("%s: %s; retrying in %.0fs", path, why, slot.delay)

    def _release(self, path: str) -> None:
        slot = self._slots.get(path)
        if slot is not None and slot.port is not None:
            try:
                slot.port.close()
            except OSError:
                pass
            slot.port = None
        self._driver.forget_port(path)


def run_loop(supervisor: Supervisor, sleep: Callable[[float], None] = time.sleep) -> None:
    try:
        while True:
            if not supervisor.step():
                sleep(IDLE_SLEEP_S)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        supervisor.close()
