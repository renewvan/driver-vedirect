# VE.Direct HEX: what we found, and what it means for this van

Evidence behind [ADR 0002](adr/0002-read-only-text-mode.md). Researched 2026-11-03 from Victron's public documents, community threads, the repo, the hub issues and a read-only look at the Pi. Anything not verified is marked **unverified**.

## 1. Setup checked

| Item | Finding | How |
| --- | --- | --- |
| Host | Raspberry Pi 4 Model B Rev 1.5, Linux 6.18 aarch64, Docker | `ssh renewvan`, read-only commands |
| Adapter | Duppa USB quad-port VE.Direct (FT4232H, 4 ports `if00`..`if03`) plus one FTDI FT232R cable and a u-blox GNSS | `/dev/serial/by-id`, `lsusb` |
| Wiring | Every port is a full UART with TX and RX, so a write is physically possible | FT4232H / FT232R are bidirectional UARTs |
| Container | `renewvan-vedirect` v0.1.0 runs with `/dev` mounted and tty device-cgroup rules | `docker ps`, `hub/docker-compose.yml` |
| Port open mode | `serial.Serial(path, baud, timeout=0)`, read/write; pyserial has no read-only mode | `driver_vedirect/driver.py:224` |
| Write calls | None in `driver_vedirect/` (no `.write(`) | grep |
| Devices | SmartShunt (PID 0xC035), SmartSolar MPPT (0xA056), Orion XS (0xA3F0), Blue Smart AC charger (0xA330, user-modded VE.Direct header) | hub issue #107 |

Not checked: whether the Duppa cable's TX line to each device is actually wired. A text-only capture does not prove TX works.

## 2. Two protocols on one port

VE.Direct Protocol 3.32 describes **text mode** (the device broadcasts all run-time fields periodically, read-only) and **HEX mode** (read and write, for example change settings). Two implementations exist:

- **Older products:** start in text mode. A valid HEX message switches the port to HEX mode, and it stays there while HEX messages keep arriving. After several seconds with none, it returns to text. Some send asynchronous `:A...` HEX messages on their own, which can interrupt a text frame.
- **Newer products:** text mode stays active whatever HEX does.

The protocol document says to check each product's own document for which applies. The VE.Direct FAQ (Q7) adds that after a HEX message "the Victron product will stop sending its usual TEXT frames for a few seconds", that continuous HEX can mean text frames never arrive, and that there is no minimum HEX rate.

**Unverified:** which implementation each of our four devices uses. We only have the BMV-7xx HEX document in hand; the SmartShunt, MPPT, Orion XS and Blue Smart documents were not readable or not found.

HEX frame shape: `:` command nibble, little-endian data, check byte, `\n`. Response codes include 3 unknown command; error codes 0x01 unknown id, 0x02 write to a read-only value, 0x04 parameter out of range.

## 3. What HEX can write, from the documents we could read

### BMV-7xx (the SmartShunt family likely shares this register style; the SmartShunt-specific document was not found)

| Register | Meaning | Notes |
| --- | --- | --- |
| 0x1000 | Battery capacity, 1..9999 Ah | the value `capacity_ah` would come from |
| 0x1001 | Charged voltage | 0..95 in 0.1 V |
| 0x1002 | Tail current | % |
| 0x1003 / 0x1004 / 0x1005 | Charged detection time / charge efficiency / Peukert | change SOC calculation |
| 0x1008 / 0x1009 | Relay low-SOC set / clear | |
| 0x0320..0x032D | Voltage, SOC and temperature alarms | |
| 0x034D..0x034F | Relay mode, invert, state | relay control |
| 0x102C | Synchronize (write-only command) | resets SOC to 100% |
| 0x0004 | Restore defaults (write-only command) | |
| 0x1030 | Clear history (write-only command) | |

Reads that text mode cannot give: consumed Ah and history counters (0x0300..0x0311: deepest discharge, cycles, min/max voltage, seconds since full, synchronizations, alarm counts).

The BMV document states no write-cycle limit. A community thread on an MPPT warns that frequent writes could wear non-volatile memory (unofficial, **unverified**).

### SmartSolar MPPT

Community threads and the MPPT HEX document (Rev 16/17, mirrored publicly) show charge-control registers: 0x200E network mode, 0x2015 charge current limit (100 mA units), 0x2001 charge voltage set-point (only meaningful in charge-master / BMS mode), and 0x200C link device state (writable). The load-output register 0xEDAD was reported read-only on an MPPT 100/20. A Victron representative was reported as saying HEX "is not supported anymore" while a Victron community answer says it is still available. **Unverified** which of these apply to the van's MPPT.

### Orion XS

An Orion XS HEX document exists (0xA3F0 12V/12V-50A, 0xA3F1 1400) with get/set and asynchronous items, including equalise and storage flags and remote on/off through RX. A community report (October 2026) says an Orion XS 70 sends undocumented asynchronous HEX codes (0xEE10, 0xEE11, 0xED56, 0xE000), so the document is not up to date. The PDF could not be parsed here, so register detail is **unverified**.

### Blue Smart AC charger

No register map found. Whether it accepts the same HEX set is **unverified**. PID 0xA330 is in no primary source (hub issue #108).

## 4. Is a write needed for this van?

No.

- The dashboard and logger display and record; nothing in the hub sets a charger or shunt value.
- `driver-venus` never wrote to Venus OS or the devices (no write or command path in its code), so read-only is parity.
- The omitted topics are the only value HEX would add, and each has another source:
  - `capacity_ah` / `remaining_ah`: capacity is a setting you already chose; keep it in VictronConnect.
  - `yield_history_wh`: comes from the logger database.
- Settings changes are done with VictronConnect over Bluetooth, outside the hub.

## 5. Could the driver write if we wanted it to?

Yes, technically: the ports are bidirectional, the container sees them, and the port is opened read/write. Nothing in the OS blocks a write. Only the absence of a write path in the code does.

If a write feature is ever wanted, a new ADR must settle:

1. Per device, whether text mode stays active while HEX is used (otherwise ADR 0001 clears live values during polling).
2. How a write is authorised. The MQTT path is currently unauthenticated (hub issues #47, #49, #52, #53).
3. Which registers are allowed, with ranges, and how a value is confirmed after writing.
4. The write-cycle limit of each register, from the product's own document.

A narrower first step is **read-only HEX**: HEX get commands only, for shunt capacity and history. That keeps "no writes", but still needs item 1 answered.

## 6. Open questions

- Which text/HEX implementation do the four devices use?
- Does the Duppa adapter's TX actually reach each device (would a HEX get be answered)?
- Is there a SmartShunt-specific HEX document, and what do PIDs 0xC035 and 0xA330 support?
- Does the van's MPPT firmware still implement the HEX registers above?

## 7. Sources

- Victron, [VE.Direct Protocol 3.32](https://www.victronenergy.nl/upload/documents/VE.Direct-Protocol-3.32.pdf)
- Victron, [VE.Direct protocol FAQ](https://www.victronenergy.com/live/vedirect_protocol:faq)
- Victron, [BMV-7xx HEX Protocol](https://www.victronenergy.de/upload/documents/BMV-7xx-HEX-Protocol.pdf)
- Victron, [Orion XS HEX protocol](https://www.victronenergy.de/upload/documents/Orion-XS-HEX-protocol.pdf) (not parsed)
- Victron community: [Orion XS HEX documentation thread](https://community.victronenergy.com/t/orion-xs-ve-direct-hex-protocol-documentation/33686); [SmartSolar current limit via HEX](https://communityarchive.victronenergy.com/questions/173192/set-current-limit-in-smartsolar-via-vedirect-hex-h.html); [MPPT load current via VE.Direct](https://communityarchive.victronenergy.com/questions/326801/set-mppt-load-current-through-vedirect.html)
- Hub issues #106, #107, #108, #111, #124 in `renewvan/hub`
