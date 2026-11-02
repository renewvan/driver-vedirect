# driver-vedirect

Domain glossary for driver-vedirect: what each published field *means*,
independent of the Python that computes it.

## Language

**PID**:
The VE.Direct product id (`0xA056`), mapped to an entity and model in `pids.json`.

**Instance id**:
The `<id>` in `renewvan/<entity>/<id>/...`. The lowercased `SER#` of the device; a SmartShunt
sends none, so it is the by-id interface tail of its port (`...-if02-port0` becomes `if02`), and
failing that the `[device] id` fallback. Two serial-less shunts must therefore sit on distinct
`ifNN` adapter ports, or they would share one id.

**Supervisor**:
The loop in `discovery.py` that scans the by-id directory, opens and polls every matching port,
and retries failed ones with doubling backoff. Devices are identified by what they report, never
by which port they use.

**Silence / stale**:
A device with no valid, mappable live block for 10 s (`STALE_AFTER_S`). Its live properties are
cleared once with empty retained payloads; identity stays. A PID-less history block or a block
with no readable field does not count. See `docs/adr/0001-clear-live-values-on-silence.md`.

**`source`**:
Charger identity property naming what feeds it (`alternator` for the Orion XS, `ac` for the Blue
Smart), taken from `pids.json`, published with `driver` and `name`; not a live value.

**`device_names`**:
`[device.<id>] name = ...` sections of the INI, keyed by lowercased instance id (serial or
interface tail); they override the default `name` (the model) of that instance.

**`pv_current_a`**:
Derived as `PPV / VPV`; VE.Direct text mode has no PV current field.

**`yield_today_wh`**:
`H20` (0.01 kWh) times 10.

**`charge_state`**:
VE.Direct `CS`, mapped with the same table as driver-venus; 11 and 245 are `unknown`.
