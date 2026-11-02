"""Discovery and reconnect, through the one seam: a fake by-id directory, fake serial ports that
can fail or disappear, a fake publisher and a fake clock, all driven via `Supervisor.step`."""

import json

from driver_vedirect.config import AppConfig, DeviceConfig, MqttConfig, SerialConfig
from driver_vedirect.discovery import Supervisor
from driver_vedirect.driver import Driver

BY_ID = "/dev/serial/by-id"
GLOBS = ("usb-VE_*", "usb-FTDI_*")


def solar_block(serial: str, power: int = 21) -> bytes:
    fields = [
        ("PID", "0xA056"), ("V", "13330"), ("I", "1500"), ("VPV", "45070"),
        ("PPV", str(power)), ("CS", "3"), ("SER#", serial), ("H20", "14"),
    ]  # fmt: skip
    block = b"".join(f"\r\n{k}\t{v}".encode() for k, v in fields) + b"\r\nChecksum\t"
    return block + bytes([(256 - sum(block)) % 256])


class FakePort:
    def __init__(self, data: bytes = b"") -> None:
        self.data, self.broken, self.closed = data, False, False

    def read(self, size: int = 4096) -> bytes:
        if self.broken:
            raise OSError("device disappeared")
        out, self.data = self.data[:size], self.data[size:]
        return out

    def close(self) -> None:
        self.closed = True


class FakeSerialWorld:
    """The by-id directory plus whatever is plugged into it."""

    def __init__(self) -> None:
        self.plugged: dict[str, FakePort] = {}
        self.open_attempts: list[str] = []
        self.refuse_open = False

    def plug(self, name: str, data: bytes = b"") -> FakePort:
        self.plugged[name] = FakePort(data)
        return self.plugged[name]

    def list_dir(self, path: str) -> list[str]:
        assert path == BY_ID
        return list(self.plugged)

    def open(self, path: str, baud: int) -> FakePort:
        name = path.removeprefix(BY_ID + "/")
        self.open_attempts.append(name)
        if self.refuse_open or name not in self.plugged:
            raise OSError("cannot open")
        return self.plugged[name]


class FakePublisher:
    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic: str, payload: str) -> None:
        self.retained[topic] = payload

    def get(self, topic: str):
        return json.loads(self.retained[topic])


class FakeClock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(world: FakeSerialWorld) -> tuple[Supervisor, FakePublisher, FakeClock]:
    mqtt = MqttConfig("localhost", 1883, None, None, "test", 1.0, 30.0)
    serial = SerialConfig(by_id_dir=BY_ID, port_globs=GLOBS, backoff_min_s=1.0, backoff_max_s=8.0)
    config = AppConfig(mqtt=mqtt, device=DeviceConfig("vedirect", 3000), serial=serial)
    publisher, clock = FakePublisher(), FakeClock()
    driver = Driver(publisher, config, clock=clock)
    return Supervisor(driver, config, clock, world.open, world.list_dir), publisher, clock


def test_devices_are_identified_by_serial_not_by_port_and_read_concurrently():
    world = FakeSerialWorld()
    world.plug("usb-VE_a-if00-port0", solar_block("HQ1", power=10))
    world.plug("usb-FTDI_b-if00-port1", solar_block("HQ2", power=20))
    world.plug("usb-Other_c-if00", solar_block("HQ3"))  # not matched by port_globs
    supervisor, pub, _ = make(world)

    supervisor.step()

    assert pub.get("renewvan/solar/hq1/power_w") == 10
    assert pub.get("renewvan/solar/hq2/power_w") == 20
    assert "renewvan/solar/hq3/power_w" not in pub.retained


def test_swapping_ports_changes_nothing():
    world = FakeSerialWorld()
    world.plug("usb-VE_a-if00-port0", solar_block("HQ1", power=10))
    world.plug("usb-VE_a-if00-port1", solar_block("HQ2", power=20))
    supervisor, pub, _ = make(world)
    supervisor.step()
    first = dict(pub.retained)

    swapped = FakeSerialWorld()
    swapped.plug("usb-VE_a-if00-port0", solar_block("HQ2", power=20))
    swapped.plug("usb-VE_a-if00-port1", solar_block("HQ1", power=10))
    supervisor2, pub2, _ = make(swapped)
    supervisor2.step()

    assert pub2.retained == first


def test_unplug_and_replug_recovers_with_backoff_and_no_restart():
    world = FakeSerialWorld()
    name = "usb-VE_a-if00-port0"
    world.plug(name, solar_block("HQ1", power=10))
    supervisor, pub, clock = make(world)
    supervisor.step()

    world.plugged[name].broken = True  # tty gone, by-id link not yet cleaned up
    supervisor.step()  # read fails: port dropped
    world.plug(name, solar_block("HQ1", power=55))  # cable back
    world.open_attempts.clear()
    supervisor.step()  # still inside the backoff window
    assert world.open_attempts == []
    assert pub.get("renewvan/solar/hq1/power_w") == 10

    clock.now += 1.0
    supervisor.step()
    assert pub.get("renewvan/solar/hq1/power_w") == 55


def test_failed_opens_back_off_exponentially_up_to_the_cap():
    world = FakeSerialWorld()
    world.plug("usb-VE_a-if00-port0")
    world.refuse_open = True
    supervisor, _, clock = make(world)

    waits = []
    for _ in range(6):
        world.open_attempts.clear()
        start = clock.now
        while True:
            supervisor.step()
            if world.open_attempts:
                break
            clock.now += 0.5
        waits.append(clock.now - start)
        clock.now += 0.0
    assert waits == [0.0, 1.0, 2.0, 4.0, 8.0, 8.0]


def test_one_broken_port_does_not_stop_the_others():
    world = FakeSerialWorld()
    bad = world.plug("usb-VE_a-if00-port0", solar_block("HQ1"))
    world.plug("usb-VE_a-if00-port1", solar_block("HQ2", power=20))
    bad.broken = True
    supervisor, pub, _ = make(world)

    supervisor.step()

    assert pub.get("renewvan/solar/hq2/power_w") == 20


def test_a_port_that_leaves_the_directory_is_closed_and_forgotten():
    world = FakeSerialWorld()
    name = "usb-VE_a-if00-port0"
    port = world.plug(name)
    supervisor, _, _ = make(world)
    supervisor.step()

    del world.plugged[name]  # symlink gone, port object never raised
    supervisor.step()

    assert port.closed


def test_values_of_a_fully_unplugged_adapter_still_go_stale():
    world = FakeSerialWorld()
    name = "usb-VE_a-if00-port0"
    world.plug(name, solar_block("HQ1", power=10))
    supervisor, pub, clock = make(world)
    supervisor.step()

    del world.plugged[name]
    clock.now += 11
    supervisor.step()

    assert pub.retained["renewvan/solar/hq1/power_w"] == ""
    assert pub.get("renewvan/solar/hq1/driver") == "vedirect"
