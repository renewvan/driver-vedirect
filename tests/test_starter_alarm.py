"""Starter-battery alarm: an alternator charger's input voltage held low publishes `low`."""

import json
from pathlib import Path

import pytest

from driver_vedirect.config import AppConfig, DeviceConfig, MqttConfig, StarterConfig, load_config
from driver_vedirect.driver import Driver
from driver_vedirect.starter import StarterAlarm

from tests.test_chargers import FakePort, FakePublisher, block

ALARM = "renewvan/charger/hq2538q7pre/input_voltage_alarm"


def orion(dc_in_v: str, **overrides: str) -> bytes:
    fields = {
        "PID": "0xA3F0",
        "SER#": "HQ2538Q7PRE",
        "V": "12900",
        "I": "0",
        "P": "0",
        "DC_IN_V": dc_in_v,
        "CS": "0",
        "ERR": "0",
    }
    fields.update(overrides)
    return block(**fields)


class Van:
    """A driver whose clock the test moves, fed one block at a time."""

    def __init__(self, starter: StarterConfig | None = None) -> None:
        mqtt = MqttConfig("localhost", 1883, None, None, "test", 1.0, 30.0)
        config = AppConfig(
            mqtt=mqtt, device=DeviceConfig(id="vedirect"), starter=starter or StarterConfig()
        )
        self.now = 1000.0
        self.publisher = FakePublisher()
        self.driver = Driver(self.publisher, config, clock=lambda: self.now)

    def feed(self, data: bytes, after_s: float = 0.0) -> None:
        self.now += after_s
        self.driver.poll(FakePort(data), key="if00")

    def alarm(self) -> str | None:
        payload = self.publisher.retained.get(ALARM)
        return json.loads(payload) if payload else None


def test_a_low_starter_battery_alarms_only_once_it_has_stayed_low_for_the_hold_time():
    van = Van()
    van.feed(orion("1230"))
    assert van.alarm() == "ok"

    van.feed(orion("1230"), after_s=119)
    assert van.alarm() == "ok"

    van.feed(orion("1230"), after_s=1)
    assert van.alarm() == "low"


def test_a_cranking_dip_shorter_than_the_hold_time_does_not_alarm():
    van = Van()
    van.feed(orion("1265"))
    van.feed(orion("1050"), after_s=4)  # cranking
    van.feed(orion("1410"), after_s=4)  # engine running
    van.feed(orion("1240"), after_s=200)  # engine off again: the hold starts over

    assert van.alarm() == "ok"


def test_the_alarm_clears_once_the_voltage_is_back_above_the_clear_level():
    van = Van()
    van.feed(orion("1230"))
    van.feed(orion("1230"), after_s=120)
    assert van.alarm() == "low"

    van.feed(orion("1245"), after_s=4)  # above low_v, below clear_v: still low (hysteresis)
    assert van.alarm() == "low"

    van.feed(orion("1421", I="-18400", P="-238", CS="243"), after_s=4)  # emergency charge
    assert van.alarm() == "ok"


def test_the_thresholds_come_from_the_config():
    van = Van(StarterConfig(low_v=12.0, clear_v=12.2, hold_s=10))
    van.feed(orion("1210"))
    van.feed(orion("1210"), after_s=60)
    assert van.alarm() == "ok"

    van.feed(orion("1190"), after_s=1)
    van.feed(orion("1190"), after_s=10)
    assert van.alarm() == "low"


def test_disabled_publishes_no_alarm():
    van = Van(StarterConfig(enabled=False))
    van.feed(orion("1100"))
    van.feed(orion("1100"), after_s=600)

    assert ALARM not in van.publisher.retained


def test_an_ac_charger_has_no_starter_alarm():
    van = Van()
    blue = block(PID="0xA330", **{"SER#": "HQ2528UNUHH"}, V="13290", I="0", CS="0")
    van.feed(blue)
    van.feed(blue, after_s=600)

    assert not any(t.endswith("/input_voltage_alarm") for t in van.publisher.retained)


def test_the_alarm_is_cleared_with_the_other_live_values_when_the_orion_goes_silent():
    van = Van()
    van.feed(orion("1230"))
    van.feed(orion("1230"), after_s=120)
    assert van.alarm() == "low"

    van.now += 11
    van.driver.tick()

    assert van.publisher.retained[ALARM] == ""


def test_load_config_reads_the_starter_section(tmp_path: Path):
    local = tmp_path / "config.ini"
    local.write_text("[starter]\nlow_v = 12.2\nclear_v = 12.4\nhold_s = 300\n")

    starter = load_config(local_path=local).starter

    assert starter == StarterConfig(enabled=True, low_v=12.2, clear_v=12.4, hold_s=300)


def test_load_config_defaults_match_the_dataclass():
    assert load_config().starter == StarterConfig()


def test_clear_v_below_low_v_is_refused(tmp_path: Path):
    local = tmp_path / "config.ini"
    local.write_text("[starter]\nlow_v = 12.4\nclear_v = 12.3\n")

    with pytest.raises(ValueError, match="clear_v"):
        load_config(local_path=local)


def test_state_machine_restarts_the_hold_after_a_recovery():
    alarm = StarterAlarm(StarterConfig())
    assert alarm.update(12.3, now=0) == "ok"
    assert alarm.update(12.6, now=100) == "ok"
    assert alarm.update(12.3, now=110) == "ok"
    assert alarm.update(12.3, now=229) == "ok"
    assert alarm.update(12.3, now=230) == "low"


# The SmartShunt's aux input set to "Starter battery" adds `VS` (mV) to its block.
SHUNT_BASE = "renewvan/battery/if02"


def shunt(vs: str | None) -> bytes:
    fields = {
        "PID": "0xA389",
        "V": "12950",
        "I": "-26440",
        "P": "-342",
        "CE": "-24495",
        "SOC": "933",
        "TTG": "624",
        "Alarm": "OFF",
        "AR": "0",
        "BMV": "SmartShunt 500A/50mV",
        "FW": "0420",
        "MON": "0",
    }
    if vs is not None:
        fields["VS"] = vs
    return block(**fields)


def shunt_feed(van: Van, data: bytes, after_s: float = 0.0) -> None:
    van.now += after_s
    van.driver.poll(FakePort(data), key="usb-Duppa_srl_X-if02-port0")


def shunt_value(van: Van, prop: str):
    payload = van.publisher.retained.get(f"{SHUNT_BASE}/{prop}")
    return json.loads(payload) if payload else None


def test_a_smartshunt_aux_input_on_the_starter_battery_publishes_its_voltage():
    van = Van()
    shunt_feed(van, shunt("12610"))

    assert shunt_value(van, "starter_voltage_v") == pytest.approx(12.61)
    assert shunt_value(van, "starter_voltage_alarm") == "ok"


def test_the_smartshunt_starter_voltage_raises_the_same_held_alarm():
    van = Van()
    shunt_feed(van, shunt("12300"))
    shunt_feed(van, shunt("12300"), after_s=119)
    assert shunt_value(van, "starter_voltage_alarm") == "ok"

    shunt_feed(van, shunt("12300"), after_s=1)
    assert shunt_value(van, "starter_voltage_alarm") == "low"


def test_a_smartshunt_without_vs_publishes_no_starter_topics():
    van = Van()
    shunt_feed(van, shunt(None))
    shunt_feed(van, shunt(None), after_s=600)

    assert shunt_value(van, "starter_voltage_v") is None
    assert f"{SHUNT_BASE}/starter_voltage_alarm" not in van.publisher.retained


def test_the_orion_and_the_shunt_keep_separate_alarms():
    van = Van()
    van.feed(orion("1230"))
    shunt_feed(van, shunt("12700"))
    van.feed(orion("1230"), after_s=120)
    shunt_feed(van, shunt("12700"))

    assert van.alarm() == "low"
    assert shunt_value(van, "starter_voltage_alarm") == "ok"


def test_both_alarms_validate_against_the_hub_schemas():
    from jsonschema import Draft7Validator

    schemas = Path(__file__).parent / "fixtures" / "schema"
    van = Van()
    van.feed(orion("1230"))
    shunt_feed(van, shunt("12300"))
    van.feed(orion("1230"), after_s=120)
    shunt_feed(van, shunt("12300"))

    for entity, base in (("charger", ALARM.rsplit("/", 1)[0]), ("battery", SHUNT_BASE)):
        schema = json.loads((schemas / f"{entity}.schema.json").read_text())
        instance = van.publisher.instance(base)
        assert list(Draft7Validator(schema).iter_errors(instance)) == []
    assert van.publisher.instance(SHUNT_BASE)["starter_voltage_alarm"] == "low"
