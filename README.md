# driver-vedirect

Reads Victron VE.Direct text mode over USB serial (read-only) and publishes battery, solar and charger to the renewvan MQTT bus

Read-only: the serial port is only ever read from; nothing is written to a device. Layout follows
`renewvan/driver-template`: layered `config.default.ini`/`config.ini` (`config.py`), MQTT with LWT
health and retained Driver info (`publisher.py`), VE.Direct framing (`frames.py`), the PID table
(`pids.json`, one line per device), field mapping (`mapping.py`) and the publish loop (`driver.py`).

**Status: walking skeleton.** A SmartSolar MPPT stream is published as a `solar` instance. The
battery (SmartShunt) and charger (Orion XS, Blue Smart) entities, staleness clearing, by-id port
discovery and reconnects are later tickets under hub#124. Devices in `pids.json` other than the
SmartSolar are recognised but not published yet. PIDs `0xC035` and `0xA330` come from a capture,
not a primary source (`"verified": false`).

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

The Blue Smart frames (PID 0xA330, unverified) contain FWE, SER#, V, I, T, ERR, CS and HC#; no P,
VPV or DC_IN_*. `T ---` leaves `temperature_c` absent (the charger schema has none), `HC#` is
ignored, and `ERR` is logged once per change. `[device.<serial>]` in `config.ini` (serial in
either case) accepts `name = ...` to relabel an instance.

Frames with a bad checksum are dropped; HEX messages interleaved with text blocks are ignored.

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

Deliberately not published: `charge_state`, `capacity_ah`, `remaining_ah`.
`[device.if02]` (or `[device.<serial>]`) in `config.ini` accepts `name = ...`.

## Wiring

One VE.Direct USB cable per device, 19200 8N1. Set `[device] port` (default `/dev/ttyUSB0`) in
`config.ini`.

## Development

```bash
git clone git@github.com:renewvan/driver-vedirect.git
cd driver-vedirect
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt
```

Keep conversion/decision logic in pure functions tested against fixture
values — no MQTT broker or real hardware required for any of it. Before
opening a PR: `pytest tests/ -v` must pass (same command CI runs), and any
change to a published field needs a matching
`hub/schema/solar.schema.json` update in a `hub` PR (per
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`,
the two repos are never coupled at build time, only at the schema-version
level).

## Configuration

`config.default.ini` ships every key with a default. Copy
`config.ini.example` to `config.ini` (gitignored) and override only what
differs.

## Running

```bash
pip install -r requirements.txt
python -m driver_vedirect.main --config config.ini
```

Install `driver_vedirect/systemd/driver-vedirect.service` to run at boot
(`Restart=on-failure`).

## Testing

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest tests/ -v
```

## Releasing

Tagging a GitHub release builds and publishes
`ghcr.io/<owner>/driver-vedirect:<tag>`, which `hub`'s deployment compose file pins
by tag (never builds from source — see
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`).
