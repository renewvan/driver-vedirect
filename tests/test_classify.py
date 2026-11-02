"""Classification of devices by PID or, for an unknown PID, by fields. Same seam as the solar
capture test: synthetic checksummed VE.Direct bytes in, published topics, logs and health out."""

import logging

from tests.test_solar_capture import FakeClock, FakePort, FakePublisher, make_config
from driver_vedirect.driver import Driver

HEALTH = "renewvan/vedirect/health"


def block(fields: list[tuple[str, str]]) -> bytes:
    """One text block with a correct checksum."""
    body = b"".join(f"\r\n{k}\t{v}".encode() for k, v in fields) + b"\r\nChecksum\t"
    return body + bytes([(-sum(body)) % 256])


def run(*streams: bytes):
    publisher = FakePublisher()
    driver = Driver(publisher, make_config(), clock=FakeClock())
    for stream in streams:
        driver.poll(FakePort(stream))
    return publisher, driver


def unrecognized(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if "nrecognized" in r.getMessage() or "nknown" in r.getMessage()
    ]


SOLAR = [
    ("PID", "0xFFFF"),
    ("SER#", "HQ0000SOLAR"),
    ("V", "13330"),
    ("I", "1500"),
    ("VPV", "45070"),
    ("PPV", "21"),
    ("CS", "3"),
]


def test_unknown_pid_with_pv_fields_is_published_as_solar():
    publisher, _ = run(block(SOLAR))

    solar = publisher.instance("renewvan/solar/hq0000solar")
    assert solar["power_w"] == 21
    assert solar["pv_voltage_v"] == 45.07


def test_pv_fields_win_over_every_other_rule():
    fields = SOLAR + [("DC_IN_V", "1300"), ("BMV", "SmartShunt"), ("SOC", "900"), ("CE", "0")]
    publisher, _ = run(block(fields))

    assert publisher.instance("renewvan/solar/hq0000solar")["power_w"] == 21


def test_dc_in_v_is_classified_not_unrecognized(caplog):
    orion = [
        ("PID", "0xFFFE"),
        ("SER#", "HQ0000ORION"),
        ("V", "13200"),
        ("I", "10000"),
        ("DC_IN_V", "1420"),
        ("CS", "3"),
    ]
    with caplog.at_level("INFO"):
        publisher, _ = run(block(orion))

    assert unrecognized(caplog) == []
    assert publisher.instance("renewvan/solar/hq0000orion") == {}


def test_bmv_or_soc_with_ce_is_classified_battery(caplog):
    by_bmv = [("PID", "0xFFFD"), ("BMV", "700"), ("V", "12800")]
    by_soc = [("PID", "0xFFFC"), ("SOC", "950"), ("CE", "-1200"), ("V", "12800")]
    with caplog.at_level("INFO"):
        run(block(by_bmv), block(by_soc))

    assert unrecognized(caplog) == []


def test_soc_alone_is_not_enough_for_a_battery(caplog):
    with caplog.at_level("INFO"):
        publisher, _ = run(block([("PID", "0xFFFB"), ("SOC", "950"), ("V", "12800")]))

    assert len(unrecognized(caplog)) == 1


def test_v_i_cs_without_other_markers_is_classified_charger(caplog):
    blue = [("PID", "0xFFFA"), ("SER#", "HQ0000AC"), ("V", "13800"), ("I", "5000"), ("CS", "4")]
    with caplog.at_level("INFO"):
        run(block(blue))

    assert unrecognized(caplog) == []


def test_v_i_without_cs_is_unrecognized(caplog):
    with caplog.at_level("INFO"):
        publisher, _ = run(block([("PID", "0xFFF9"), ("V", "13800"), ("I", "5000")]))

    assert len(unrecognized(caplog)) == 1


def test_unrecognized_device_warns_once_per_port_with_pid_and_fields(caplog):
    odd = block([("PID", "0xFFF8"), ("SER#", "HQ0000ODD"), ("FOO", "1"), ("BAR", "2")])
    publisher = FakePublisher()
    driver = Driver(publisher, make_config(), clock=FakeClock())
    port_a, port_b = FakePort(odd * 3), FakePort(odd)
    with caplog.at_level("INFO"):
        driver.poll(port_a)
        driver.poll(port_b)

    warnings = [r for r in unrecognized(caplog) if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # one per port
    text = warnings[0].getMessage()
    assert "0xFFF8" in text and "FOO" in text and "BAR" in text
    assert publisher.retained == {}


def test_unrecognized_device_leaves_driver_healthy(caplog):
    publisher, _ = run(block([("PID", "0xFFF7"), ("FOO", "1")]))

    assert publisher.retained.get(HEALTH, "online") != "offline"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_unverified_pids_log_one_info_on_first_sight(caplog):
    for pid in ("0xC035", "0xA330"):
        caplog.clear()
        frame = block([("PID", pid), ("SER#", "HQ0000X"), ("V", "13000"), ("I", "100")])
        with caplog.at_level("INFO"):
            run(frame * 3)
        infos = [r for r in caplog.records if r.levelno == logging.INFO and pid in r.getMessage()]
        assert len(infos) == 1, pid


def test_verified_pid_logs_no_unverified_info(caplog):
    with caplog.at_level("INFO"):
        run(
            block(
                [
                    ("PID", "0xA056"),
                    ("SER#", "HQ1"),
                    ("V", "13000"),
                    ("VPV", "40000"),
                    ("PPV", "10"),
                ]
            )
        )

    assert [r for r in caplog.records if r.levelno == logging.INFO] == []
