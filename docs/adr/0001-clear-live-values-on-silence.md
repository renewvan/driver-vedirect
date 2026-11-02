---
status: accepted
---

# Clear live values after 10 s of silence

After 10 s without a valid frame for a device, the Driver publishes an empty retained payload on each of that device's live properties (the ones its mapping publishes: voltages, currents, power, state of charge and so on). Identity properties (`driver`, `name`) and the Driver's health topic are left alone, so "unplugged" (identity present, values absent) is distinguishable from "never existed" (nothing at all). When valid frames resume, the next block republishes the live values. Clearing happens once per silence, not on every poll.

## Why

- **Silence is an invalid reading.** This is the rule in hub ADR 0008 (a Node clears a retained topic when its reading goes invalid). A VE.Direct device that stops talking would otherwise leave its last retained value on the bus, and the dashboard would show a dead number as current.
- **10 s** is more than two of the slowest block cadences we measured (the Orion XS, about one block per 4 s) and short enough that a stale value is never mistaken for live.
- **Empty retained payloads, not `null` or an `offline` flag.** The dashboard and logger already treat an empty payload as "no value", so no consumer changes.
- **Identity and health stay.** The Driver's health reflects the Driver process, not any one device; a missing cable must not turn it unhealthy.
- **Driver-local and generic.** The clock is injected and clearing tracks live properties per entity instance, so battery, solar and charger share one mechanism.

## Consequences

- A shunt's alternating live and history blocks both count as valid frames; a device that only sends corrupt blocks is treated as silent.
- The threshold is a constant (`STALE_AFTER_S`), not configuration; change it with a new ADR if a device with a slower cadence is added.
