"""Data model shared by every sensor backend and by the UI.

The objects created here are plain, immutable-ish value objects.  A sample is
produced inside the collector thread and handed to the GUI thread as a whole,
so nothing is mutated after it leaves the collector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Kind:
    """A category of measurement (temperature, fan speed, voltage, ...)."""

    name: str
    unit: str
    precision: int
    order: int


KINDS: Dict[str, Kind] = {
    "temp": Kind("temp", "°C", 1, 0),
    "fan": Kind("fan", "RPM", 0, 1),
    "pwm": Kind("pwm", "%", 0, 2),
    "volt": Kind("volt", "V", 3, 3),
    "curr": Kind("curr", "A", 3, 4),
    "power": Kind("power", "W", 2, 5),
    "freq": Kind("freq", "MHz", 0, 6),
    "load": Kind("load", "%", 0, 7),
    "memory": Kind("memory", "MB", 0, 8),
    "energy": Kind("energy", "J", 2, 9),
    "humidity": Kind("humidity", "%", 1, 10),
    "other": Kind("other", "", 2, 11),
}


def kind_of(name: str) -> Kind:
    return KINDS.get(name, KINDS["other"])


@dataclass(frozen=True)
class Stats:
    """Min / max / average accumulated since the application was started."""

    minimum: float
    maximum: float
    average: float
    count: int


class StatsStore:
    """Running min/max/average per sensor key.

    Kept deliberately tiny: a rolling sum is enough for the "average since
    start" column that HWMonitor shows, and it costs O(1) per sample.
    """

    def __init__(self) -> None:
        self._min: Dict[str, float] = {}
        self._max: Dict[str, float] = {}
        self._sum: Dict[str, float] = {}
        self._count: Dict[str, int] = {}

    def update(self, key: str, value: float) -> Stats:
        if not math.isfinite(value):
            return self.get(key) or Stats(value, value, value, 0)
        if key not in self._count:
            self._min[key] = value
            self._max[key] = value
            self._sum[key] = 0.0
            self._count[key] = 0
        elif value < self._min[key]:
            self._min[key] = value
        elif value > self._max[key]:
            self._max[key] = value
        self._sum[key] += value
        self._count[key] += 1
        return Stats(
            self._min[key], self._max[key], self._sum[key] / self._count[key], self._count[key]
        )

    def get(self, key: str) -> Optional[Stats]:
        if key not in self._count:
            return None
        return Stats(
            self._min[key], self._max[key], self._sum[key] / self._count[key], self._count[key]
        )

    def reset(self) -> None:
        self._min.clear()
        self._max.clear()
        self._sum.clear()
        self._count.clear()


@dataclass(frozen=True)
class Reading:
    """A single sensor value plus everything needed to render one tree row."""

    key: str
    label: str
    kind: str
    value: float
    #: Hardware-defined alarm point (temp*_crit / temp*_max), when published.
    limit: Optional[float] = None
    stats: Optional[Stats] = None

    @property
    def unit(self) -> str:
        return kind_of(self.kind).unit

    def format(self, value: Optional[float]) -> str:
        if value is None or not math.isfinite(value):
            return "-"
        k = kind_of(self.kind)
        text = f"{value:.{k.precision}f}"
        return f"{text} {k.unit}".strip()

    @property
    def text(self) -> str:
        return self.format(self.value)

    def is_alarm(self, temp_threshold: float) -> bool:
        """True when the row should be painted red."""
        if self.kind != "temp" or not math.isfinite(self.value):
            return False
        threshold = temp_threshold
        if self.limit is not None and math.isfinite(self.limit) and self.limit > 0:
            threshold = min(threshold, self.limit)
        return self.value >= threshold

    def with_stats(self, stats: Stats) -> "Reading":
        return replace(self, stats=stats)


@dataclass(frozen=True)
class Group:
    """One hwmon chip / GPU / lm-sensors adapter: a top level tree item."""

    key: str
    name: str
    detail: str = ""
    readings: List[Reading] = field(default_factory=list)


@dataclass(frozen=True)
class Sample:
    """The full result of one polling round."""

    groups: List[Group] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    @property
    def sensor_count(self) -> int:
        return sum(len(g.readings) for g in self.groups)


def sort_readings(readings: Iterable[Reading]) -> List[Reading]:
    """Group rows by kind (temps first, then fans, voltages, ...)."""
    return sorted(readings, key=lambda r: (kind_of(r.kind).order, _natural(r.label)))


def _natural(text: str):
    """Sort key so that `temp2` comes before `temp10`."""
    out, digits = [], ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            if digits:
                out.append((1, int(digits), ""))
                digits = ""
            out.append((0, 0, ch.lower()))
    if digits:
        out.append((1, int(digits), ""))
    return out
