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
import re
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
    baud: int = 19200


@dataclass(frozen=True)
class SerialConfig:
    """Where to look for VE.Direct cables and how to recover when one goes away."""

    by_id_dir: str = "/dev/serial/by-id"
    port_globs: tuple[str, ...] = ("usb-Duppa_srl_*", "usb-VictronEnergy_*", "usb-FTDI_*")
    backoff_min_s: float = 1.0
    backoff_max_s: float = 30.0


@dataclass(frozen=True)
class StarterConfig:
    """`[starter]`: the starter-battery alarm on an alternator charger's input voltage.

    Defaults suit a 12 V lead-acid starter battery: 12.4 V at rest is about 75% charged.
    """

    enabled: bool = True
    low_v: float = 12.4
    clear_v: float = 12.5
    hold_s: float = 120.0


@dataclass(frozen=True)
class AppConfig:
    mqtt: MqttConfig
    device: DeviceConfig
    serial: SerialConfig = field(default_factory=SerialConfig)
    starter: StarterConfig = field(default_factory=StarterConfig)
    # `[device.<serial>] name = ...`, keyed by lowercased serial: an instance label override.
    device_names: dict[str, str] = field(default_factory=dict)
    # `[device.<lookup>] id = ...`, keyed by lowercased lookup: the topic instance id to publish
    # under instead of the serial / interface tail. Validated and unique (see load_config).
    device_ids: dict[str, str] = field(default_factory=dict)


_ALIAS = re.compile(r"[a-z0-9_-]+")


def _read_device_ids(parser: configparser.ConfigParser) -> dict[str, str]:
    """Collect the `id` aliases; raise ValueError for an invalid or duplicated one."""
    ids: dict[str, str] = {}
    owner: dict[str, str] = {}  # alias -> section that claimed it
    for name, section in parser.items():
        if not name.startswith("device.") or "id" not in section:
            continue
        alias, lookup = section["id"].strip(), name[len("device.") :].lower()
        if not _ALIAS.fullmatch(alias):
            raise ValueError(
                f"[{name}] id = {alias!r} is not a valid topic instance id: use one MQTT "
                "topic segment of lowercase letters, digits, '-' and '_'"
            )
        if alias in owner:
            raise ValueError(
                f"id = {alias!r} is configured by both [{owner[alias]}] and [{name}]; "
                "each device needs its own id"
            )
        owner[alias] = name
        ids[lookup] = alias
    return ids


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

    starter = parser["starter"] if parser.has_section("starter") else {}
    defaults = StarterConfig()
    starter_config = StarterConfig(
        enabled=parser.getboolean("starter", "enabled", fallback=defaults.enabled),
        low_v=float(starter.get("low_v", defaults.low_v)),
        clear_v=float(starter.get("clear_v", defaults.clear_v)),
        hold_s=float(starter.get("hold_s", defaults.hold_s)),
    )
    if starter_config.clear_v < starter_config.low_v:
        raise ValueError(
            f"[starter] clear_v = {starter_config.clear_v} is below low_v = "
            f"{starter_config.low_v}; it must be at or above it"
        )

    device_names = {
        name[len("device.") :].lower(): section["name"]
        for name, section in parser.items()
        if name.startswith("device.") and section.get("name")
    }

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
            baud=device.getint("baud", 19200),
        ),
        serial=SerialConfig(
            by_id_dir=serial.get("by_id_dir", "/dev/serial/by-id"),
            port_globs=globs,
            backoff_min_s=serial.getfloat("backoff_min_s", 1.0),
            backoff_max_s=serial.getfloat("backoff_max_s", 30.0),
        ),
        starter=starter_config,
        device_names=device_names,
        device_ids=_read_device_ids(parser),
    )
