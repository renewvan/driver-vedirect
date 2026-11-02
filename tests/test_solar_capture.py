"""The one test seam: bytes in through a fake serial port, MQTT topics out
through a fake publisher, time from a fake clock. No test reaches into the
parser, the PID table or the mapper."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from driver_vedirect.config import AppConfig, DeviceConfig, MqttConfig
from driver_vedirect.driver import Driver

FIXTURES = Path(__file__).parent / "fixtures"
SOLAR_SCHEMA = json.loads((FIXTURES / "schema" / "solar.schema.json").read_text())
CAPTURE = (FIXTURES / "smartsolar-mppt.raw").read_bytes()
BASE = "renewvan/solar/hq2001qure8"


class FakePort:
    """Serial port that hands out its bytes in fixed-size chunks, then b'' (a read timeout)."""

    def __init__(self, data: bytes, chunk: int = 4096) -> None:
        self._data, self._chunk = data, chunk

    def read(self, size: int = 4096) -> bytes:
        out, self._data = self._data[: self._chunk], self._data[self._chunk :]
        return out


class FakePublisher:
    """Keeps the last retained payload per topic, like a broker would."""

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


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_config() -> AppConfig:
    mqtt = MqttConfig("localhost", 1883, None, None, "test", 1.0, 30.0)
    return AppConfig(mqtt=mqtt, device=DeviceConfig(id="vedirect"))


def play(data: bytes, chunk: int = 4096) -> FakePublisher:
    publisher = FakePublisher()
    driver = Driver(publisher, make_config(), clock=FakeClock())
    port = FakePort(data, chunk)
    while port._data:
        driver.poll(port)
    return publisher


def test_smartsolar_capture_publishes_a_schema_valid_solar_instance():
    solar = play(CAPTURE).instance(BASE)

    assert solar["power_w"] == 21
    assert solar["battery_voltage_v"] == 13.33
    assert solar["battery_current_a"] == 1.5
    assert solar["pv_voltage_v"] == 45.07
    assert solar["pv_current_a"] == pytest.approx(21 / 45.07, abs=0.001)
    assert solar["yield_today_wh"] == 140
    assert solar["charge_state"] == "bulk"
    assert solar["driver"] == "vedirect"
    assert list(Draft7Validator(SOLAR_SCHEMA).iter_errors(solar)) == []


def test_split_frames_are_reassembled():
    assert play(CAPTURE, chunk=7).instance(BASE) == play(CAPTURE).instance(BASE)


def test_bad_checksum_frame_is_dropped():
    first_end = CAPTURE.index(b"Checksum\t") + 10
    corrupt = CAPTURE[:first_end] + CAPTURE[first_end:].replace(b"I\t1600", b"I\t9999", 1)

    seen = play(corrupt).instance(BASE)

    assert seen["battery_current_a"] == 1.5  # last good frame wins, corrupt one never published


def test_bad_checksum_warnings_are_rate_limited(caplog):
    first_end = CAPTURE.index(b"Checksum\t") + 10
    bad = CAPTURE[:first_end].replace(b"V\t13330", b"V\t13331")
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, make_config(), clock=clock)

    with caplog.at_level("WARNING"):
        for _ in range(3):
            driver.poll(FakePort(bad))
        assert len(caplog.records) == 1
        clock.now += 61
        driver.poll(FakePort(bad))
        assert len(caplog.records) == 2


def test_corrupt_only_stream_publishes_nothing():
    first_end = CAPTURE.index(b"Checksum\t") + 10
    assert play(CAPTURE[:first_end].replace(b"V\t13330", b"V\t13331")).retained == {}


def test_hex_frames_between_and_inside_text_blocks_are_ignored():
    hex_frame = b":7F0ED0064\n"
    mid = CAPTURE.index(b"VPV")
    noisy = CAPTURE[:mid] + hex_frame + CAPTURE[mid:]
    first_end = noisy.index(b"Checksum\t") + 10
    noisy = noisy[:first_end] + hex_frame + noisy[first_end:]

    assert play(noisy).instance(BASE) == play(CAPTURE).instance(BASE)


def poll_silence(driver, seconds: float, clock: FakeClock) -> None:
    clock.now += seconds
    driver.poll(FakePort(b""))
    driver.tick()  # the Supervisor ticks after polling every port, ports or not


def test_ten_seconds_of_silence_clears_live_properties_but_keeps_identity():
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, make_config(), clock=clock)
    driver.poll(FakePort(CAPTURE))
    live = {k for k in publisher.instance(BASE) if k not in ("driver", "name")}

    poll_silence(driver, 9.9, clock)
    assert publisher.instance(BASE).keys() >= live  # not stale yet

    poll_silence(driver, 0.2, clock)
    for prop in live:
        assert publisher.retained[f"{BASE}/{prop}"] == ""
    assert publisher.instance(BASE) == {"driver": "vedirect", "name": "SmartSolar MPPT"}


def test_clearing_happens_once_per_silence():
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, make_config(), clock=clock)
    driver.poll(FakePort(CAPTURE))
    poll_silence(driver, 11, clock)
    sent = []
    publisher.publish = lambda t, p: sent.append((t, p))
    poll_silence(driver, 11, clock)
    assert sent == []


def test_frames_resuming_republish_live_values():
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, make_config(), clock=clock)
    driver.poll(FakePort(CAPTURE))
    before = publisher.instance(BASE)
    poll_silence(driver, 11, clock)

    driver.poll(FakePort(CAPTURE))

    assert publisher.instance(BASE) == before


def test_continuous_frames_never_clear():
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, make_config(), clock=clock)
    for _ in range(5):
        driver.poll(FakePort(CAPTURE))
        clock.now += 8
    assert publisher.instance(BASE)["power_w"] == 21
