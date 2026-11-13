"""Starter-battery alarm for an alternator (DC-DC) charger: its input side is the starter battery.

`low` once the input voltage has stayed below `low_v` for `hold_s` seconds, so a cranking dip or
one bad reading does not raise it; back to `ok` as soon as it reaches `clear_v` (the engine running
or an emergency charge lifts it well above). Between the two levels the state holds (hysteresis),
so a battery resting near `low_v` does not flap.
"""

from __future__ import annotations

from driver_vedirect.config import StarterConfig


class StarterAlarm:
    def __init__(self, config: StarterConfig) -> None:
        self._config = config
        self._state = "ok"
        self._low_since: float | None = None

    def update(self, voltage_v: float, now: float) -> str:
        if voltage_v >= self._config.clear_v:
            self._state, self._low_since = "ok", None
        elif voltage_v < self._config.low_v:
            if self._low_since is None:
                self._low_since = now
            if now - self._low_since >= self._config.hold_s:
                self._state = "low"
        else:
            self._low_since = None  # not low any more: a later dip starts its own hold
        return self._state
