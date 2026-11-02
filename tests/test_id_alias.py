"""`[device.<lookup>] id = ...` through the one seam: config text in, topics out via a fake
publisher, payloads validated against the hub schemas."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from driver_vedirect.config import load_config
from driver_vedirect.driver import STALE_AFTER_S, Driver
from tests import test_shunt_capture as shunt
from tests.test_chargers import FakePort, FakePublisher, blue, block, make_config
from tests.test_solar_capture import FakeClock

SCHEMAS = Path(__file__).parent / "fixtures" / "schema"


def config_from(tmp_path, text: str):
    local = tmp_path / "config.ini"
    local.write_text(text)
    return load_config(local_path=local)


def test_alias_publishes_a_charger_under_the_chosen_id_and_nowhere_else(tmp_path):
    config = config_from(tmp_path, "[device.HQ2528UNUHH]\nid = shore\nname = Shore charger\n")
    publisher = FakePublisher()
    driver = Driver(publisher, config, clock=FakeClock())
    port = FakePort(blue())
    driver.poll(port)

    charger = publisher.instance("renewvan/charger/shore")
    assert charger["name"] == "Shore charger"  # name keeps working independently
    assert charger["driver"] == "vedirect"
    assert charger["source"] == "ac"
    assert charger["voltage_v"] == pytest.approx(13.29, abs=0.011)
    schema = json.loads((SCHEMAS / "charger.schema.json").read_text())
    assert list(Draft7Validator(schema).iter_errors(charger)) == []
    assert not any("hq2528unuhh" in t for t in publisher.retained)


def test_alias_on_the_serial_less_shunt_is_keyed_by_interface_tail(tmp_path):
    config = config_from(tmp_path, "[device.IF02]\nid = house\n")

    publisher = shunt.play(shunt.live(), config=config)

    assert publisher.instance("renewvan/battery/house")["power_w"] == 12
    assert not any("/if02/" in t for t in publisher.retained)


def test_alias_applies_to_stale_clearing(tmp_path):
    config = config_from(tmp_path, "[device.if02]\nid = house\n")
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, config, clock=clock)
    driver.poll(shunt.NamedPort(shunt.live()))
    assert publisher.retained["renewvan/battery/house/power_w"] == "12"

    clock.now += STALE_AFTER_S + 1
    driver.tick()

    assert publisher.retained["renewvan/battery/house/power_w"] == ""
    assert publisher.retained["renewvan/battery/house/driver"] != ""  # identity stays


def test_alias_applies_to_identity_republish_on_reconnect(tmp_path):
    config = config_from(tmp_path, "[device.if02]\nid = house\n")
    publisher = FakePublisher()
    driver = Driver(publisher, config, clock=FakeClock())
    driver.poll(shunt.NamedPort(shunt.live()))
    publisher.retained.clear()

    driver.on_mqtt_connect()

    assert publisher.instance("renewvan/battery/house")["name"] == "SmartShunt"
    assert list(publisher.retained) and all("/house/" in t for t in publisher.retained)


def test_err_change_log_uses_the_alias(tmp_path, caplog):
    config = config_from(tmp_path, "[device.hq2528unuhh]\nid = shore\n")
    driver = Driver(FakePublisher(), config, clock=FakeClock())

    with caplog.at_level("WARNING"):
        driver.poll(FakePort(blue(ERR="2")))

    assert any("shore" in r.getMessage() and "ERR" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad", ["", "Up", "a/b", "a+b", "#", "a b", "a.b"])
def test_invalid_alias_is_rejected_at_load_with_a_clear_error(tmp_path, bad):
    with pytest.raises(ValueError, match=r"device\.hq1.*id"):
        config_from(tmp_path, f"[device.hq1]\nid = {bad}\n")


def test_alias_allows_lowercase_digits_dash_underscore(tmp_path):
    config = config_from(tmp_path, "[device.hq1]\nid = house-2_a\n")
    assert config.device_ids == {"hq1": "house-2_a"}


def test_two_sections_with_the_same_alias_are_rejected_naming_both(tmp_path):
    text = "[device.hq1]\nid = house\n[device.IF02]\nid = house\n"
    with pytest.raises(ValueError, match=r"house.*hq1.*if02|house.*if02.*hq1"):
        config_from(tmp_path, text)


def test_alias_colliding_with_another_devices_real_id_refuses_the_second(tmp_path, caplog):
    # HQ1 is aliased to "hq2" while a second charger really has serial HQ2. First seen wins; the
    # second is refused (error logged once, nothing published); the driver keeps running.
    config = config_from(tmp_path, "[device.hq1]\nid = hq2\n")
    publisher = FakePublisher()
    driver = Driver(publisher, config, clock=FakeClock())

    with caplog.at_level("ERROR"):
        driver.poll(FakePort(blue(**{"SER#": "HQ1"})))
        driver.poll(FakePort(blue(**{"SER#": "HQ2"}, V="99000")))
        driver.poll(FakePort(blue(**{"SER#": "HQ2"}, V="99000")))
        driver.poll(FakePort(blue(**{"SER#": "HQ1"})))

    assert publisher.instance("renewvan/charger/hq2")["voltage_v"] == pytest.approx(13.29, abs=0.011)
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "hq2" in errors[0].getMessage()


def test_collision_is_detected_in_either_arrival_order(tmp_path, caplog):
    config = config_from(tmp_path, "[device.hq1]\nid = hq2\n")
    publisher = FakePublisher()
    driver = Driver(publisher, config, clock=FakeClock())

    with caplog.at_level("ERROR"):
        driver.poll(FakePort(blue(**{"SER#": "HQ2"}, V="99000")))
        driver.poll(FakePort(blue(**{"SER#": "HQ1"})))

    assert publisher.instance("renewvan/charger/hq2")["voltage_v"] == pytest.approx(99, abs=0.1)
    assert any(r.levelname == "ERROR" for r in caplog.records)
