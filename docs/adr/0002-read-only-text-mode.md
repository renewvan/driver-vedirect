---
status: accepted
---

# Read-only, VE.Direct text mode only

The Driver reads VE.Direct text mode and never writes to a device: no HEX protocol, no settings, no control, no Bluetooth. HEX frames that devices interleave with text blocks are ignored (and excluded from the checksum). Anything that needs HEX is not published; see the omitted topics below.

## Why

- **A hub that cannot change a charger cannot misconfigure one.** The owner's requirement (hub issue #124, story 19) is "no software can ever write to a charger or shunt". The four devices (SmartShunt, SmartSolar MPPT, Orion XS, Blue Smart AC charger) are the van's charging and measurement chain. Their settings are the ones Victron's HEX documents expose as writable, for example battery capacity, charged voltage, tail current and Peukert exponent on the BMV/SmartShunt family (BMV-7xx HEX protocol, "Monitor settings registers"), and charge-control registers on the MPPT. A wrong or repeated write corrupts state of charge or charge behaviour, and the only person watching is a dashboard.
- **Writing is physically and technically possible here, so the guarantee has to be a decision, not a limitation.** On the Pi (Raspberry Pi 4, checked on the host) the cables sit behind a Duppa quad-port adapter (FT4232H) plus one FTDI cable, all full UARTs with TX and RX. The `vedirect` compose profile mounts `/dev` and allows the tty majors. The Driver opens ports with `serial.Serial(path, baud, timeout=0)`, which is read/write; pyserial has no read-only mode. Nothing stops a write except that the code has no write path.
- **Nothing the hub does needs a write.** The dashboard and logger only display and record. Settings are changed with VictronConnect over Bluetooth, which stays outside the hub. `driver-venus`, which this Driver replaces, never wrote to Venus OS or the devices either, so read-only is parity, not a regression.
- **HEX is not free even for reads.** Victron documents two implementations (VE.Direct Protocol 3.32): older products switch to HEX mode on a valid HEX message and stop sending text for several seconds, resuming only after HEX traffic stops; newer ones keep text mode active regardless. The FAQ warns that continuous HEX means text frames may never arrive. Which implementation each of our four devices uses is in their own product documents, and we have not confirmed it. Polling HEX could therefore blank the stream the Driver depends on, and ADR 0001 would then clear live values.
- **The HEX documentation is incomplete.** The Orion XS sends asynchronous HEX codes that its published document does not list (0xEE10, 0xEE11, 0xED56, 0xE000, community report), and the SmartShunt (PID 0xC035) and Blue Smart (0xA330) are in no primary source we found (hub issue #108). Supporting HEX means writing a second parser and a request/response layer against moving documentation, with no way to verify it besides the real van.
- **Text mode is enough for parity.** It carries every value `driver-venus` published except the topics listed below.

## Omitted because they need HEX

Recorded in hub issue #111 and the README "Differences from driver-venus" table:

- `battery/<id>/capacity_ah` and `remaining_ah`: bank capacity is register 0x1000, a HEX setting. `soc_pct` and `time_to_go_s` still come from `SOC` and `TTG`.
- `solar/<id>/yield_history_wh`: text mode only has today and yesterday (`H20` to `H23`). The history comes from the database (logger).

`battery/<id>/charge_state` is also omitted, but not for HEX reasons: a shunt has no equivalent and inferring one from a charger would be a guess.

## Consequences

- The "never writes" property is enforced by code and review, not by the port. Keep it true with a test that fails if the Driver gains a serial `write` call, and flag any new `.write(` in review. Opening the device read-only is not possible through pyserial; do not claim it.
- Write support (settings, remote on/off, alarm acknowledge) needs a new ADR that supersedes this one. That ADR must first confirm per device whether text mode stays active during HEX, and must decide how a write is authorised, since the MQTT path currently has no authentication (hub issues #47 and #49).
- Reading shunt capacity or the 30-day history over HEX is the likeliest first extension. It can use HEX get commands only without breaking the "no writes" rule, but it still hits the text-mode interruption above. That is a separate decision.
- A wrong value on the shunt, charger or MPPT is fixed in VictronConnect, not through the hub.

## Sources

Full findings, register tables and open questions: [vedirect-hex-research.md](../vedirect-hex-research.md).

- Victron, VE.Direct Protocol 3.32 (text mode, older vs newer implementations).
- Victron, VE.Direct protocol FAQ (HEX interrupts text frames; TX level differs by product).
- Victron, BMV-7xx HEX Protocol (writable monitor settings, write-only Synchronize / Restore Defaults / Clear History commands, error 0x02 on read-only registers). This is the BMV document; we did not find a SmartShunt-specific one.
- Hub issues #106, #108, #111, #124.
