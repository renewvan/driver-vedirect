"""Layered INI config: config.default.ini (shipped) + config.ini (local
override).

Same two-file pattern as every renewvan/driver-* repo (see
driver-tank/driver_tank/config.py): config.default.ini ships every key with a
safe default and is never edited at an install; config.ini is a gitignored,
install-local file that overrides only what differs.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.default.ini"


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    reconnect_min_delay: float
    reconnect_max_delay: float


@dataclass(frozen=True)
class DeviceConfig:
    id: str  # topic path segment, e.g. renewvan/vedirect/<id>/...
    update_interval_ms: int
    baud: int = 19200


@dataclass(frozen=True)
class SerialConfig:
    """Where to look for VE.Direct cables and how to recover when one goes away."""

    by_id_dir: str = "/dev/serial/by-id"
    port_globs: tuple[str, ...] = ("usb-VictronEnergy_*", "usb-FTDI_*")
    backoff_min_s: float = 1.0
    backoff_max_s: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    mqtt: MqttConfig
    device: DeviceConfig
    serial: SerialConfig = field(default_factory=SerialConfig)


def load_config(
    default_path: Path = DEFAULT_CONFIG_PATH, local_path: Path | None = None
) -> AppConfig:
    """Load config.default.ini, then layer config.ini (if present) on top."""
    parser = configparser.ConfigParser()
    parser.read(default_path)
    if local_path is not None and local_path.exists():
        parser.read(local_path)
    else:
        logger.info("No local config override at %s; using defaults only", local_path)

    mqtt = parser["mqtt"]
    device = parser["device"]
    serial = parser["serial"]
    globs = tuple(g.strip() for g in serial.get("port_globs", "").split(",") if g.strip())

    return AppConfig(
        mqtt=MqttConfig(
            host=mqtt.get("host", "localhost"),
            port=mqtt.getint("port", 1883),
            username=mqtt.get("username") or None,
            password=mqtt.get("password") or None,
            client_id=mqtt.get("client_id", "driver-vedirect"),
            reconnect_min_delay=mqtt.getfloat("reconnect_min_delay", 1.0),
            reconnect_max_delay=mqtt.getfloat("reconnect_max_delay", 30.0),
        ),
        device=DeviceConfig(
            id=device.get("id", "vedirect"),
            update_interval_ms=device.getint("update_interval_ms", 3000),
            baud=device.getint("baud", 19200),
        ),
        serial=SerialConfig(
            by_id_dir=serial.get("by_id_dir", "/dev/serial/by-id"),
            port_globs=globs,
            backoff_min_s=serial.getfloat("backoff_min_s", 1.0),
            backoff_max_s=serial.getfloat("backoff_max_s", 30.0),
        ),
    )
