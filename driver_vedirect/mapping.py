"""VE.Direct fields -> entity properties (units per the spec: mV, mA, 0.01 kWh)."""

from __future__ import annotations

# VE.Direct `CS`; same table as driver-venus's CHARGER_STATE_CODES. 11 (power supply) and
# 245 (starting up) are deliberately unknown, as in driver-venus.
CHARGE_STATES: dict[int, str] = {
    0: "off",
    2: "fault",
    3: "bulk",
    4: "absorption",
    5: "float",
    6: "storage",
    7: "equalize",
    246: "absorption",
    247: "equalize",
    248: "float",
    252: "external_control",
}


def charge_state(raw: str) -> str:
    try:
        return CHARGE_STATES.get(int(raw), "unknown")
    except ValueError:
        return "unknown"


def map_solar(fields: dict[str, str]) -> dict[str, object]:
    """SmartSolar MPPT block -> `solar` live properties. Fields that are absent stay absent."""
    out: dict[str, object] = {}
    try:
        if "PPV" in fields:
            out["power_w"] = int(fields["PPV"])
        if "V" in fields:
            out["battery_voltage_v"] = int(fields["V"]) / 1000
        if "I" in fields:
            out["battery_current_a"] = int(fields["I"]) / 1000
        if "VPV" in fields:
            vpv_mv = int(fields["VPV"])
            out["pv_voltage_v"] = vpv_mv / 1000
            # VE.Direct has no PV current field: derive it from the reported PV power.
            if "PPV" in fields:
                out["pv_current_a"] = (
                    round(int(fields["PPV"]) * 1000 / vpv_mv, 3) if vpv_mv else 0.0
                )
        if "H20" in fields:
            out["yield_today_wh"] = int(fields["H20"]) * 10  # 0.01 kWh -> Wh
        if "CS" in fields:
            out["charge_state"] = charge_state(fields["CS"])
    except ValueError:
        return {}  # a field we cannot read means we do not trust the block
    return out


# Venus caps TimeToGo at ~10 days (864000 s); a shunt's 65534 min would otherwise exceed it.
MAX_TIME_TO_GO_S = 864000


def map_battery(fields: dict[str, str]) -> dict[str, object]:
    """SmartShunt block -> `battery` live properties; `None` means "has no value, clear it".

    `TTG -1` (infinite) and `T ---` (no probe) are None. `charge_state`, `capacity_ah` and
    `remaining_ah` are deliberately never published (a shunt has no stage; history is in the DB).
    """
    out: dict[str, object] = {}
    try:
        if "SOC" in fields:
            out["soc_pct"] = int(fields["SOC"]) / 10  # per mille
        if "V" in fields:
            out["voltage_v"] = int(fields["V"]) / 1000
        if "I" in fields:
            out["current_a"] = int(fields["I"]) / 1000  # positive = charging
        if "P" in fields:
            out["power_w"] = int(fields["P"])
        if "TTG" in fields:
            minutes = int(fields["TTG"])
            out["time_to_go_s"] = None if minutes < 0 else min(minutes * 60, MAX_TIME_TO_GO_S)
        if "T" in fields:
            out["temperature_c"] = None if fields["T"].strip() == "---" else int(fields["T"])
    except ValueError:
        return {}  # a field we cannot read means we do not trust the block
    return out
