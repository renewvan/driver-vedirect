# driver-vedirect

Reads Victron VE.Direct text mode over USB serial, **read-only**, and publishes `battery`,
`solar` and `charger` instances to the renewvan MQTT bus, so the hub can read a van's Victron
devices directly instead of through a Venus OS Pi and `driver-venus`. Driver key `vedirect`.

Supported devices (see `driver_vedirect/pids.json`, one line per device):

| Device           | PID               | Entity (source)          |
| ---------------- | ----------------- | ------------------------ |
| SmartShunt       | `0xC035`, `0xA389`| `battery`                |
| SmartSolar MPPT  | `0xA056`          | `solar`                  |
| Orion XS         | `0xA3F0`-`0xA3FF` | `charger` (`alternator`) |
| Blue Smart       | `0xA330`          | `charger` (`ac`)         |

A device with an unknown PID is classified by its fields when they are unambiguous (`VPV`/`PPV`
solar, `DC_IN_V` alternator charger, `BMV` or `SOC`+`CE` battery, `V`+`I`+`CS` AC charger).
Otherwise one WARNING per port names the PID and field names, nothing is published and the
Driver stays healthy.

Nothing is ever written to a device: no HEX protocol, no control, no Bluetooth (why:
[ADR 0002](docs/adr/0002-read-only-text-mode.md)). Layout follows
`renewvan/driver-template`: layered `config.default.ini`/`config.ini` (`config.py`), MQTT with
LWT health and retained Driver info (`publisher.py`), VE.Direct framing (`frames.py`), the PID
table (`pids.json`, `pids.py`), field mapping (`mapping.py`), by-id discovery and reconnect
(`discovery.py`) and the publish loop (`driver.py`).

## Topics published

`renewvan/solar/<serial>/<property>`, retained JSON; `<serial>` is the lowercased `SER#`.

| Property            | Source                                   | Kind     |
| ------------------- | ---------------------------------------- | -------- |
| `driver`, `name`    | Driver key, PID table model              | identity |
| `power_w`           | `PPV` (W)                                | live     |
| `battery_voltage_v` | `V` (mV)                                 | live     |
| `battery_current_a` | `I` (mA, + = charging)                   | live     |
| `pv_voltage_v`      | `VPV` (mV)                               | live     |
| `pv_current_a`      | derived `PPV / VPV` (no such VE.Direct field) | live |
| `yield_today_wh`    | `H20` (0.01 kWh)                         | live     |
| `charge_state`      | `CS`, same table as driver-venus         | live     |

`renewvan/charger/<serial>/<property>`: the Orion XS (`source` `alternator`) and Blue Smart AC
charger (`source` `ac`).

| Property          | Source                                              | Kind     |
| ----------------- | --------------------------------------------------- | -------- |
| `driver`, `name`, `source` | Driver key, PID table model (or `[device.<serial>] name`), PID table | identity |
| `voltage_v`       | `V` (mV)                                            | live     |
| `current_a`       | `I` (mA)                                            | live     |
| `power_w`         | `P` (W) when sent, else derived `V x I` (Blue Smart, and Orion XS without `P`) | live |
| `input_voltage_v` | `DC_IN_V` (0.01 V), Orion XS only                   | live     |
| `charge_state`    | `CS`, same table as driver-venus                    | live     |

`ERR` is logged once per change (a healthy first `0` is not news). `T ---` leaves
`temperature_c` absent (the charger schema has none). `HC#` is ignored. Frames with a bad
checksum are dropped; HEX messages interleaved with text blocks are ignored.

### SmartShunt: `renewvan/battery/<id>/<property>`

`<id>` is the lowercased `SER#`; the SmartShunt sends none, so it is the by-id interface tail of its
port (`...-if02-port0` -> `if02`). That id depends on the cable staying in the same adapter port.
Its alternating live and history (`H1..H18`) blocks are merged by key; only the fields below are
published.

| Property        | Source                                                  |
| --------------- | ------------------------------------------------------- |
| `soc_pct`       | `SOC` (per mille / 10)                                  |
| `voltage_v`     | `V` (mV)                                                |
| `current_a`     | `I` (mA, + = charging)                                  |
| `power_w`       | `P` (W)                                                 |
| `time_to_go_s`  | `TTG` (min x 60, capped at 864000 like Venus); `-1` (infinite) leaves it absent, clearing an earlier value |
| `temperature_c` | `T`; `---` (no probe) leaves it absent                  |
| `time_since_full_charge_s` | `H9` (s, from the history block merged by key) |

`charge_state`, `capacity_ah` and `remaining_ah` are deliberately not published.

### Charge state

`CS` maps with the `driver-venus` table: 0 `off`, 2 `fault`, 3 `bulk`, 4 and 246 `absorption`,
5 and 248 `float`, 6 `storage`, 7 and 247 `equalize`, 252 `external_control`. Anything else,
including 11 (power supply) and 245 (starting up), is `unknown`.

### Silence clears live values

After 10 s without a valid frame for a device, its live properties are cleared with empty
retained payloads; `driver`, `name`, `source` and the Driver's health stay, so "unplugged" is
distinguishable from "never existed". The next valid block republishes them. See
`docs/adr/0001-clear-live-values-on-silence.md` and hub ADR 0008.

## Topics v1 does not match

Compared with what `driver-venus` publishes for the same devices. Acceptance covers the shared
topics only.

| Topic or behavior                         | `driver-venus`                                 | `driver-vedirect` v1 |
| ----------------------------------------- | ---------------------------------------------- | -------------------- |
| `battery/<id>/charge_state`               | system state fanned out to every battery       | not published (a shunt has no stage) |
| `battery/<id>/capacity_ah`, `remaining_ah`| configured capacity and consumed Ah            | not published (needs HEX; history comes from the database) |
| `solar/<id>/yield_history_wh`             | day log from the MPPT history                  | not published (needs HEX; history comes from the database) |
| BMS topics (`cell_voltages_v`, `protections`, limits and the rest) | from `dbus-serialbattery`     | not applicable, no BMS on VE.Direct |
| `charger/<id>/power_w` (Blue Smart)       | not set (Venus has AC input power only)        | derived `V x I`, output power |
| `charger/<id>/power_w` (Orion XS, no `P`) | `Dc/0/Power`                                   | derived `V x I` |
| Instance ids                              | Venus device instance (for example `290`)      | lowercased `SER#`, or the by-id interface tail for the SmartShunt; the primary-battery pick and logger history keys change on migration |
| `name`                                    | `CustomName`, else `ProductName`               | PID table model, or `[device.<id>] name` |
| Charge states 11 and 245                  | `unknown`                                      | `unknown` (same) |

## What the Blue Smart frames contain

From the capture (`tests/fixtures/blue-smart.raw`):

`PID` `0xA330`, `FWE` `0369FF`, `SER#`, `V`, `I`, `T`, `ERR`, `CS`, `HC#`.

There is no `P`, no `VPV` and no `DC_IN_*`. In the capture `T` is `---` (no sensor), `ERR` is
`0`, `CS` is `3` (bulk). That is why the classification fallback treats `V`+`I`+`CS` with no
solar or alternator field as an AC charger, and why `power_w` is derived. `HC#` (a counter of
unknown meaning) is ignored.

## Unverified facts

These came from the capture and inference, not a primary Victron source. Treat them as open
until checked on the van:

- **PIDs `0xC035` (SmartShunt) and `0xA330` (Blue Smart)** appear in no Victron document. They
  are marked `"verified": false` in `pids.json` and logged at INFO on first sight.
- **`HC#`** on the Blue Smart: meaning unknown; ignored.
- **`P` sign convention**: whether the shunt's and Orion XS's `P` is positive when charging, like
  `I`, is assumed. The dashboard spot-check against the device display should confirm it.
- **Orion XS block cadence**: about one block per 4 s is inferred from the capture, not
  documented. The 10 s staleness threshold is more than two of these.
- **`port_globs` default** (see below) is a guess.

## Configuration

`config.default.ini` ships every key with a default; copy `config.ini.example` to `config.ini`
(gitignored) and override only what differs.

| Key                                | Default                           | Meaning |
| ---------------------------------- | --------------------------------- | ------- |
| `[mqtt]` `host`, `port`, `username`, `password`, `client_id`, `reconnect_min_delay`, `reconnect_max_delay` | as in `config.default.ini` | broker; paho reconnects with backoff |
| `[device]` `id`                    | `vedirect`                        | fallback id for a SmartShunt whose port has no interface tail |
| `[device]` `baud`                  | `19200`                           | VE.Direct is 19200 8N1 |
| `[serial]` `by_id_dir`             | `/dev/serial/by-id`               | directory scanned for adapters |
| `[serial]` `port_globs`            | `usb-Duppa_srl_*, usb-VictronEnergy_*, usb-FTDI_*` | comma-separated `fnmatch` patterns on by-id names |
| `[serial]` `backoff_min_s`         | `1.0`                             | first retry delay for a port that fails |
| `[serial]` `backoff_max_s`         | `30.0`                            | the doubling stops here |
| `[device.<id>]` `name`             |                                   | relabel one instance; `<id>` is the lowercased `SER#` or the interface tail (`if02`), any case |
| `[device.<id>]` `id`               |                                   | publish that device as `renewvan/<entity>/<this id>/...` instead of its serial / interface tail; one topic segment of `a-z`, `0-9`, `-`, `_`; unique across sections |

Example: give the SmartShunt on adapter port `if02` a friendly topic and label.

```ini
[device.if02]
id = house
name = House battery
```

It now publishes `renewvan/battery/house/...` (the lookup key stays `if02`; `name` is independent).
An invalid or duplicated `id` makes the Driver refuse to start with a message naming the section(s).
If an alias equals another device's real id of the same entity (say `id = hq2` while a charger
with serial `HQ2` exists), the device seen first keeps the id; the other one gets one ERROR in the
log and nothing is published for it, while the Driver keeps running.

**Changing an `id` later splits history.** Loggers key their history by topic, so the old id's
data stays under the old name and the new id starts a fresh series. Pick ids once. The old
retained topics are not cleared; remove them from the broker by hand.

**Confirm `port_globs` on the Pi.** The van uses one Duppa quad-port adapter. The default includes
`usb-Duppa_srl_*`, matching the by-id name seen in the SmartShunt capture
(`usb-Duppa_srl_USB_QUADPORT_VE.DIRECT_...-if02-port0`), but run `ls /dev/serial/by-id` on the Pi to
confirm it, and set the hub's `vedirect` profile gate to the same globs. Narrow the globs when other
USB serial adapters share the directory.

### Discovery and backoff

The Driver lists `by_id_dir` every step and opens every name matching `port_globs`. Devices are
identified by the PID and `SER#` in their frames, never by port order, so replugging cables is
safe. A port that fails to open or read is closed and retried after `backoff_min_s`, doubling
to `backoff_max_s` (1, 2, 4, 8, 16, 30, 30 ... s); a port that delivers bytes again resets the
delay. A name that disappears from the directory is released. A missing directory (no adapter
ever plugged in) is an empty scan, not an error. The loop sleeps 0.2 s when nothing arrived.
The container and the systemd install share this code (`driver.run()`).

### Republish and rate limiting

There is deliberately no per-property dedupe and no rate limit in v1:

- **Live values** are published (retained, QoS 1) from every valid block, so each device
  republishes about once per second (the Orion XS about every 4 s) and a restarted broker or
  new subscriber is healed by the next block. A mapped value that has no value (`TTG -1`,
  `T ---`) clears its topic once.
- **Identity** (`driver`, `name`, `source`) is published the first time a device is seen and
  again on every MQTT (re)connect, after Driver info and `online`.
- **Driver info and health** follow `docs/authoring-a-driver.md`: info first, then `online`, last
  will `offline`, info never cleared.
- **Logs are rate limited**: bad-checksum blocks at most one WARNING per minute (a count), an
  unknown device one WARNING per port, `ERR` only on change.

If broker load matters, a minimum publish interval per device is the next step.

## Wiring

VE.Direct USB cables, 19200 8N1, into the Duppa quad-port adapter (or one cable per device).
The SmartShunt's id depends on its cable staying in the same adapter port.

## Development

```bash
git clone git@github.com:renewvan/driver-vedirect.git
cd driver-vedirect
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt
```

Tests have one seam: bytes in through a fake serial port, topics out through a fake publisher,
with a fake clock. Captured frames are in `tests/fixtures/*.raw`; published payloads are
validated against the hub entity schemas copied to `tests/fixtures/schema/`. Before opening a
PR: `pytest tests/ -v` and `ruff check . && ruff format --check .`. A change to a published
field needs a matching `hub/schema/*.schema.json` update in a `hub` PR (per
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`, the repos are
coupled only at the schema level).

## Running

```bash
pip install -r requirements.txt
python -m driver_vedirect.main --config config.ini
```

### systemd (no container)

```bash
sudo mkdir -p /opt/driver-vedirect && sudo chown "$USER" /opt/driver-vedirect
git clone https://github.com/renewvan/driver-vedirect /opt/driver-vedirect
cd /opt/driver-vedirect && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.ini.example config.ini   # set [mqtt] host and [serial] port_globs
sed "s|@SERVICE_USER@|$(whoami)|" driver_vedirect/systemd/driver-vedirect.service \
  | sudo tee /etc/systemd/system/driver-vedirect.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now driver-vedirect
```

The unit runs `python -m driver_vedirect.main` (the same `run()` and discovery as the
container), as the Pi's login user (the unit ships `User=@SERVICE_USER@`, filled in by the `sed` above, as in
the hub's plugins) with
`SupplementaryGroups=dialout` for access to `/dev/ttyUSB*`. `Restart=on-failure`.

### Container

`ghcr.io/renewvan/driver-vedirect:<tag>`. The container needs the serial devices: mount `/dev`
with cgroup device rules for majors 188 (ttyUSB) and 166 (ttyACM). The hub's `vedirect` compose
profile does this and starts only when a by-id device exists.

## Testing

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest tests/ -v
```

## Releasing

Start a release from **Actions -> Release -> Run workflow**, or
`gh workflow run release.yml -f bump=minor` (`patch`, `major`, or `-f version=0.2.0`). One run
works out the next version from the latest `v*` tag, tags `main`, creates the GitHub release and
builds and publishes `ghcr.io/renewvan/driver-vedirect:<tag>` (and `:latest`), which `hub`'s
compose file pins by tag (never builds from source; see
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`). A beta from a
feature branch: dispatch on that branch with **prerelease** ticked; it publishes only its own tag,
never `:latest`, and writes nothing to `main`. The process lives in `hub`
(`hub/docs/adr/0015-one-release-process-via-reusable-workflows.md`).

The workflow passes the tag as the `DRIVER_VERSION` build argument, which the Dockerfile promotes
to an environment variable. With none set (a checkout run, a local build) Driver info reports
`dev`. Nothing else carries a version.

## Driver info

`manifest.json` (repo root) is the single source of the static fields (`driver` `vedirect`,
`product`, `source`, `publishes` `battery`/`solar`/`charger`, `manufacturer`, `homepage`);
`version` and `board_serial` are forbidden in it. The image copies it beside the module. Driver
info is retained on `renewvan/vedirect/info`, health on `renewvan/vedirect/health` (last will
`offline`). CI validates the manifest against the hub schema at a pinned ref, and
`tests/test_driver_info.py` checks it offline against a copy of that schema.
