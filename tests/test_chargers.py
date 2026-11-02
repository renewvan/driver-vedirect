"""Chargers through the one seam: bytes in via a fake port, topics out via a fake publisher."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from driver_vedirect.config import AppConfig, DeviceConfig, MqttConfig, load_config
from driver_vedirect.driver import Driver

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "schema" / "charger.schema.json").read_text())
ORION = (FIXTURES / "orion-xs.raw").read_bytes()
BLUE = (FIXTURES / "blue-smart.raw").read_bytes()
ORION_BASE = "renewvan/charger/hq2538q7pre"
BLUE_BASE = "renewvan/charger/hq2528unuhh"


class FakePort:
    def __init__(self, data: bytes, chunk: int = 4096) -> None:
        self._data, self._chunk = data, chunk

    def read(self, size: int = 4096) -> bytes:
        out, self._data = self._data[: self._chunk], self._data[self._chunk :]
        return out


class FakePublisher:
    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic: str, payload: str) -> None:
        self.retained[topic] = payload

    def instance(self, base: str) -> dict:
        prefix = base + "/"
        return {
            t[len(prefix) :]: json.loads(p)
            for t, p in self.retained.items()
            if t.startswith(prefix) and p != ""
        }


def make_config() -> AppConfig:
    mqtt = MqttConfig("localhost", 1883, None, None, "test", 1.0, 30.0)
    return AppConfig(mqtt=mqtt, device=DeviceConfig(id="vedirect", update_interval_ms=3000))


def play(data: bytes, config: AppConfig | None = None) -> FakePublisher:
    publisher = FakePublisher()
    driver = Driver(publisher, config or make_config(), clock=lambda: 1000.0)
    port = FakePort(data)
    while port._data:
        driver.poll(port)
    return publisher


def block(**fields: str) -> bytes:
    """A checksummed text block (insertion order kept, PID first by convention)."""
    body = b"".join(f"\r\n{k}\t{v}".encode() for k, v in fields.items()) + b"\r\nChecksum\t"
    return body + bytes([(256 - sum(body)) % 256])


def blue(**overrides: str) -> bytes:
    fields = {
        "PID": "0xA330",
        "SER#": "HQ2528UNUHH",
        "V": "13290",
        "I": "30000",
        "T": "---",
        "ERR": "0",
        "CS": "3",
        "HC#": "702382",
    }
    fields.update(overrides)
    return block(**{k: v for k, v in fields.items() if v is not None})


def assert_valid(instance: dict) -> None:
    assert list(Draft7Validator(SCHEMA).iter_errors(instance)) == []


def test_orion_xs_capture_is_an_alternator_charger():
    charger = play(ORION).instance(ORION_BASE)

    assert charger["source"] == "alternator"
    assert charger["input_voltage_v"] == pytest.approx(12.32, abs=0.011)  # DC_IN_V, 0.01 V
    assert charger["voltage_v"] == pytest.approx(13.29, abs=0.011)
    assert charger["current_a"] == 0
    assert charger["power_w"] == 0
    assert charger["charge_state"] == "off"
    assert charger["driver"] == "vedirect"
    assert charger["name"] == "Orion XS"
    assert_valid(charger)


def test_orion_xs_without_p_derives_power_from_voltage_times_current():
    stream = block(PID="0xA3F0", **{"SER#": "HQ2538Q7PRE"}, V="13000", I="10000", DC_IN_V="1400")

    charger = play(stream).instance(ORION_BASE)

    assert charger["power_w"] == 130
    assert_valid(charger)


def test_orion_xs_reported_p_wins_over_derivation():
    stream = block(PID="0xA3F0", **{"SER#": "HQ2538Q7PRE"}, V="13000", I="10000", P="125")

    assert play(stream).instance(ORION_BASE)["power_w"] == 125


def test_blue_smart_capture_is_an_ac_charger():
    charger = play(BLUE).instance(BLUE_BASE)

    assert charger["source"] == "ac"
    assert charger["voltage_v"] == pytest.approx(13.3, abs=0.011)
    assert charger["current_a"] == 30
    assert charger["power_w"] == pytest.approx(30 * charger["voltage_v"], abs=0.1)
    assert charger["charge_state"] == "bulk"
    assert charger["name"] == "Blue Smart"
    assert "input_voltage_v" not in charger
    assert_valid(charger)


def test_blue_smart_ignores_hc_and_absent_temperature():
    charger = play(blue()).instance(BLUE_BASE)

    assert "temperature_c" not in charger
    assert not any(k.startswith("HC") or "hc" in k for k in charger)
    assert set(charger) == {
        "driver",
        "name",
        "source",
        "voltage_v",
        "current_a",
        "power_w",
        "charge_state",
    }


def test_err_is_logged_on_change_only(caplog):
    stream = blue(ERR="0") + blue(ERR="2") + blue(ERR="2") + blue(ERR="2") + blue(ERR="0")

    with caplog.at_level("INFO"):
        play(stream)

    err_logs = [r.getMessage() for r in caplog.records if "ERR" in r.getMessage()]
    assert len(err_logs) == 2  # 0 -> 2, then 2 -> 0; the initial 0 and repeats are silent
    assert "2" in err_logs[0] and "0" in err_logs[1]


def test_device_section_config_names_the_instance_by_serial(tmp_path):
    local = tmp_path / "config.ini"
    local.write_text("[device.HQ2528UNUHH]\nname = Shore charger\n")

    charger = play(blue(), load_config(local_path=local)).instance(BLUE_BASE)

    assert charger["name"] == "Shore charger"
