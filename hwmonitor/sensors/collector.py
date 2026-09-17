"""Combines every backend into a single periodic sample."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List

from . import hwmon, lmsensors, nvidia
from .model import Group, Sample, StatsStore

SOURCE_AUTO = "auto"
SOURCE_HWMON = "hwmon"
SOURCE_SENSORS = "sensors"


@dataclass
class CollectorConfig:
    hwmon_root: str = hwmon.DEFAULT_HWMON_ROOT
    drm_root: str = hwmon.DEFAULT_DRM_ROOT
    #: "auto" uses sysfs and only falls back to lm_sensors when it finds nothing.
    source: str = SOURCE_AUTO
    enable_nvidia: bool = True
    command_timeout: float = 5.0


class Collector:
    """Polls all enabled backends and keeps the min/max/avg history."""

    def __init__(self, config: CollectorConfig | None = None) -> None:
        self.config = config or CollectorConfig()
        self.stats = StatsStore()
        self._nvidia_available = nvidia.available() if self.config.enable_nvidia else False

    # -- public API -------------------------------------------------------
    def reset_stats(self) -> None:
        self.stats.reset()

    def sample(self) -> Sample:
        groups: List[Group] = []
        errors: List[str] = []
        sources: List[str] = []

        if self.config.source in (SOURCE_AUTO, SOURCE_HWMON):
            found, problems = self._safe(
                lambda: hwmon.read_hwmon(self.config.hwmon_root, self.config.drm_root),
                "hwmon",
            )
            groups.extend(found)
            errors.extend(problems)
            if found:
                sources.append(f"hwmon ({len(found)})")

        want_sensors = self.config.source == SOURCE_SENSORS or (
            self.config.source == SOURCE_AUTO and not groups
        )
        if want_sensors:
            found, problems = self._safe(
                lambda: lmsensors.read_lmsensors(self.config.command_timeout), "lm_sensors"
            )
            groups.extend(found)
            errors.extend(problems)
            if found:
                sources.append(f"lm_sensors ({len(found)})")

        if self._nvidia_available:
            found, problems = self._safe(
                lambda: nvidia.read_nvidia(self.config.command_timeout), "nvidia-smi"
            )
            groups.extend(found)
            errors.extend(problems)
            if found:
                sources.append(f"nvidia-smi ({len(found)})")

        groups = [self._apply_stats(group) for group in groups]
        return Sample(groups=groups, errors=_unique(errors), sources=sources)

    # -- internals --------------------------------------------------------
    def _safe(self, call, label: str):
        """Never let one broken backend take the whole application down."""
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - a backend must not be fatal
            return [], [f"{label}: 予期しないエラー ({exc.__class__.__name__}: {exc})"]

    def _apply_stats(self, group: Group) -> Group:
        readings = [
            reading.with_stats(self.stats.update(reading.key, reading.value))
            for reading in group.readings
        ]
        return replace(group, readings=readings)


def _unique(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
