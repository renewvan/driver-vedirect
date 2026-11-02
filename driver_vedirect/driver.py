"""Publish loop: TODO describe VE.Direct read -> publish ->
renewvan/vedirect/<id>/<property>.

This is a scaffolded stub -- see the design checklist in README.md. Fill
in, in order:

1. A reader for the physical hardware/feed (see driver-tank's adc.py for a
   device that owns the sensing, or driver-venus's Victron MQTT subscription
   for a device that remaps an existing feed -- Q1 of the design round
   decides which shape this is).
2. The fields this device publishes, matching a hub/schema/<entity>.schema.json
   contract (new entity) or an existing one (Q2/Q3 of the design round).
3. Identity fields (published once at startup) vs live fields (republished
   every poll) -- see driver-tank's publish_identity()/read_and_publish()
   split for the pattern.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from driver_vedirect.config import AppConfig
from driver_vedirect.driver_info import build_info_payload
from driver_vedirect.publisher import Publisher

logger = logging.getLogger(__name__)

# This Driver's key: its repo name minus `driver-`, equal to manifest.json's `driver` and to the
# `renewvan/<key>/info` segment. Entity instances name it on `renewvan/vedirect/<id>/driver`.
DRIVER_KEY = "vedirect"


def _topic(device_id: str, prop: str) -> str:
    return f"renewvan/vedirect/{device_id}/{prop}"


def read_and_publish(publisher: Publisher, config: AppConfig) -> None:
    """TODO: read the hardware/feed and publish live fields via
    publisher.publish(_topic(config.device.id, "<property>"), payload).
    """
    raise NotImplementedError(
        "Implement the VE.Direct read+publish step (see this module's docstring)"
    )


def run(config: AppConfig) -> None:
    # Board serial is read once, here: Publisher republishes this same payload on every reconnect.
    info_payload = build_info_payload(env=os.environ, cpuinfo_path=Path("/proc/cpuinfo"))
    publisher = Publisher(config.mqtt, info_payload)
    publisher.connect()
    try:
        while True:
            read_and_publish(publisher, config)
            time.sleep(config.device.update_interval_ms / 1000)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        publisher.disconnect()
