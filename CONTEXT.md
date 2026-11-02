# driver-vedirect

Domain glossary for driver-vedirect: what each published field *means*,
independent of the Python that computes it.

## Language

**PID**:
The VE.Direct product id (`0xA056`), mapped to an entity and model in `pids.json`.

**Instance id**:
The lowercased `SER#` of the device; the `<id>` in `renewvan/solar/<id>/...`.

**`pv_current_a`**:
Derived as `PPV / VPV`; VE.Direct text mode has no PV current field.

**`yield_today_wh`**:
`H20` (0.01 kWh) times 10.

**`charge_state`**:
VE.Direct `CS`, mapped with the same table as driver-venus; 11 and 245 are `unknown`.
