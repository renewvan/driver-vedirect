"""SmartShunt -> `battery`, through the one seam: bytes in via a fake serial port, topics out via a
fake publisher. Payloads are validated against the hub's battery schema."""

import json
from pathlib import Path

from jsonschema import Draft7Validator

from driver_vedirect.driver import Driver
from tests.test_solar_capture import FakeClock, FakePort, FakePublisher, make_config

FIXTURES = Path(__file__).parent / "fixtures"
BATTERY_SCHEMA = json.loads((FIXTURES / "schema" / "battery.schema.json").read_text())
CAPTURE = (FIXTURES / "smartshunt.raw").read_bytes()
BASE = "renewvan/battery/if02"
BY_ID = "/dev/serial/by-id/usb-Duppa_srl_USB_QUADPORT_VE.DIRECT_Du3078cUEF-if02-port0"


class NamedPort(FakePort):
    """FakePort that knows which by-id path it was opened from, like serial.Serial.name."""

    def __init__(self, data: bytes, name: str = BY_ID, chunk: int = 4096) -> None:
        super().__init__(data, chunk)
        self.name = name


def block(fields: list[tuple[str, str]]) -> bytes:
    """A text block with a correct checksum."""
    body = b"".join(f"\r\n{k}\t{v}".encode() for k, v in fields) + b"\r\nChecksum\t"
    return body + bytes([(256 - sum(body)) % 256])


def live(**over: str) -> bytes:
    fields = {"PID": "0xC035", "V": "13323", "I": "893", "P": "12", "SOC": "679", "TTG": "-1"}
    fields.update(over)
    return block(list(fields.items()))


def play(data: bytes, name: str = BY_ID, chunk: int = 4096, config=None) -> FakePublisher:
    publisher = FakePublisher()
    driver = Driver(publisher, config or make_config(), clock=FakeClock())
    port = NamedPort(data, name, chunk)
    while port._data:
        driver.poll(port)
    return publisher


def test_smartshunt_capture_publishes_a_schema_valid_battery_instance():
    battery = play(CAPTURE).instance(BASE)

    assert battery["soc_pct"] == 67.9
    assert battery["voltage_v"] == 13.321  # last live block of the capture
    assert battery["current_a"] == 0.863
    assert battery["power_w"] == 11
    assert battery["driver"] == "vedirect"
    assert battery["name"] == "SmartShunt"
    assert list(Draft7Validator(BATTERY_SCHEMA).iter_errors(battery)) == []


def test_history_block_does_not_lose_or_overwrite_live_values_and_h_keys_are_not_published():
    battery = play(CAPTURE).instance(BASE)

    assert not any(key.startswith("H") for key in battery)
    assert set(battery) == {"driver", "name", "soc_pct", "voltage_v", "current_a", "power_w"}


def test_charge_state_capacity_and_remaining_are_deliberately_not_published():
    battery = play(live() + live()).instance(BASE)

    for prop in ("charge_state", "capacity_ah", "remaining_ah"):
        assert prop not in battery


def test_ttg_minus_one_is_infinite_and_leaves_time_to_go_absent():
    assert "time_to_go_s" not in play(live(TTG="-1")).instance(BASE)


def test_ttg_is_published_in_seconds():
    assert play(live(TTG="90")).instance(BASE)["time_to_go_s"] == 5400


def test_ttg_is_capped_like_venus():
    assert play(live(TTG="65534")).instance(BASE)["time_to_go_s"] == 864000


def test_time_to_go_is_cleared_when_the_estimate_goes_away():
    publisher = play(live(TTG="90") + live(TTG="-1"))

    assert publisher.retained[f"{BASE}/time_to_go_s"] == ""


def test_no_probe_leaves_temperature_absent():
    assert "temperature_c" not in play(live(T="---")).instance(BASE)


def test_probe_temperature_is_published_and_schema_valid():
    battery = play(live(T="23")).instance(BASE)

    assert battery["temperature_c"] == 23
    assert list(Draft7Validator(BATTERY_SCHEMA).iter_errors(battery)) == []


def test_negative_current_and_power_mean_discharging():
    battery = play(live(I="-12500", P="-166", TTG="120")).instance(BASE)

    assert battery["current_a"] == -12.5
    assert battery["power_w"] == -166
    assert list(Draft7Validator(BATTERY_SCHEMA).iter_errors(battery)) == []


def test_split_frames_and_hex_interleave_do_not_change_the_result():
    noisy = CAPTURE.replace(b"PID", b":7F0ED0064\nPID", 1)

    assert play(noisy, chunk=5).instance(BASE) == play(CAPTURE).instance(BASE)


def test_instance_id_is_the_by_id_interface_tail():
    other = BY_ID.replace("-if02-", "-if05-")

    assert play(live(), name=other).instance("renewvan/battery/if05")["power_w"] == 12


def test_serial_number_wins_over_the_interface_tail_when_the_device_sends_one():
    publisher = play(live(**{"SER#": "HQ1234ABCDE"}))

    assert publisher.instance("renewvan/battery/hq1234abcde")["power_w"] == 12


def test_device_section_named_by_interface_tail_sets_the_name(tmp_path):
    from driver_vedirect.config import load_config

    local = tmp_path / "config.ini"
    local.write_text("[device.if02]\nname = House battery\n")
    config = load_config(local_path=local)

    assert play(live(), config=config).instance(BASE)["name"] == "House battery"


def test_identity_is_republished_after_an_mqtt_reconnect_but_not_before():
    publisher = FakePublisher()
    driver = Driver(publisher, make_config(), clock=FakeClock())
    driver.poll(NamedPort(live()))
    publisher.retained.clear()  # a broker that lost its retained state

    driver.poll(NamedPort(live()))
    assert f"{BASE}/name" not in publisher.retained  # same session: identity is not repeated

    driver.on_mqtt_connect()
    driver.poll(NamedPort(live()))
    assert publisher.instance(BASE)["name"] == "SmartShunt"
    assert publisher.instance(BASE)["driver"] == "vedirect"
