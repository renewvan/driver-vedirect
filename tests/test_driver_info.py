"""Behavioural tests for the published Driver info payload.

Seam: build_info_payload() -- manifest + environment + /proc/cpuinfo in,
retained-info JSON out. No MQTT broker or hardware required.
"""

import json
from pathlib import Path

from driver_vedirect.driver import DRIVER_KEY
from driver_vedirect.driver_info import build_info_payload


def _info(env: dict[str, str], cpuinfo: Path | None = None) -> dict:
    return json.loads(
        build_info_payload(env=env, cpuinfo_path=cpuinfo or Path("/nonexistent/cpuinfo"))
    )


def test_info_identifies_the_driver_and_what_it_publishes() -> None:
    info = _info({})

    assert info["driver"] == DRIVER_KEY
    assert info["publishes"] == ["battery", "solar", "charger"]
    assert info["product"]
    assert info["source"]


def test_info_carries_the_stamped_version() -> None:
    assert _info({"DRIVER_VERSION": "v1.2.3"})["version"] == "v1.2.3"


def test_info_version_is_dev_when_unset_or_blank() -> None:
    assert _info({})["version"] == "dev"
    assert _info({"DRIVER_VERSION": ""})["version"] == "dev"


def test_info_board_serial_comes_from_cpuinfo_serial_line(tmp_path: Path) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("Hardware\t: BCM2711\nSerial\t\t: 10000000a1b2c3d4\n")

    assert _info({}, cpuinfo)["board_serial"] == "10000000a1b2c3d4"


def test_info_omits_board_serial_when_unreadable_empty_or_all_zeros(tmp_path: Path) -> None:
    assert "board_serial" not in _info({}, tmp_path / "does-not-exist")
    for text in ("Serial\t\t: \n", "Serial\t\t: 0000000000000000\n", "processor\t: 0\n"):
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text(text)
        assert "board_serial" not in _info({}, cpuinfo)


def test_manifest_validates_against_the_hub_manifest_schema_and_publishes_every_entity() -> None:
    from jsonschema import Draft7Validator

    root = Path(__file__).parent
    schema = json.loads((root / "fixtures" / "schema" / "driver-manifest.schema.json").read_text())
    manifest = json.loads((root.parent / "manifest.json").read_text())

    assert list(Draft7Validator(schema).iter_errors(manifest)) == []
    assert manifest["driver"] == DRIVER_KEY
