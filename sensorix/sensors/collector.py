"""Combines every backend into a single periodic sample."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from . import disk, hwmon, lmsensors, net, nvidia
from .devinfo import DEFAULT_CPUINFO, DEFAULT_DMI_ROOT, DeviceNamer
from .model import Group, Sample, StatsStore, sort_readings
from .pciids import SEARCH_PATHS, PciIds

SOURCE_AUTO = "auto"
SOURCE_HWMON = "hwmon"
SOURCE_SENSORS = "sensors"


@dataclass
class CollectorConfig:
    hwmon_root: str = hwmon.DEFAULT_HWMON_ROOT
    drm_root: str = hwmon.DEFAULT_DRM_ROOT
    net_root: str = net.DEFAULT_NET_ROOT
    block_root: str = disk.DEFAULT_BLOCK_ROOT
    dmi_root: str = DEFAULT_DMI_ROOT
    cpuinfo: str = DEFAULT_CPUINFO
    pci_ids_paths: Tuple[str, ...] = field(default_factory=lambda: tuple(SEARCH_PATHS))
    #: "auto" uses sysfs and only falls back to lm_sensors when it finds nothing.
    source: str = SOURCE_AUTO
    enable_nvidia: bool = True
    enable_net: bool = True
    enable_disk: bool = True
    command_timeout: float = 5.0


class Collector:
    """Polls all enabled backends and keeps the min/max/avg history."""

    def __init__(self, config: CollectorConfig | None = None) -> None:
        self.config = config or CollectorConfig()
        self.stats = StatsStore()
        self.namer = DeviceNamer(
            dmi_root=self.config.dmi_root,
            cpuinfo=self.config.cpuinfo,
            pci_db=PciIds(self.config.pci_ids_paths),
        )
        self._net = net.NetReader(self.config.net_root, self.namer)
        self._disk = disk.DiskReader(self.config.block_root, self.namer)
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
                lambda: hwmon.read_hwmon(
                    self.config.hwmon_root, self.config.drm_root, self.namer
                ),
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

        if self.config.enable_disk:
            found, problems = self._safe(self._disk.read, "disk")
            groups.extend(found)
            errors.extend(problems)
            if found:
                sources.append(f"disk ({len(found)})")

        if self.config.enable_net:
            found, problems = self._safe(self._net.read, "net")
            groups.extend(found)
            errors.extend(problems)
            if found:
                sources.append(f"net ({len(found)})")

        groups = [self._apply_stats(group) for group in _merge(groups)]
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


def _merge(groups: List[Group]) -> List[Group]:
    """Fold groups that describe the same physical device into one.

    An NVMe drive shows up twice - once as an hwmon chip with its temperature,
    once as a block device with its throughput - and a single "Samsung SSD 980
    PRO 1TB" node holding both is what a user expects to see.
    """
    merged: List[Group] = []
    index: Dict[str, int] = {}

    for group in groups:
        key: Optional[str] = group.merge_key
        if key is None:
            merged.append(group)
            continue
        position = index.get(key)
        if position is None:
            index[key] = len(merged)
            merged.append(group)
            continue

        existing = merged[position]
        # Prefer the name that was resolved to a model rather than a driver
        # name; if both were, the first one wins.
        name = existing.name
        detail = existing.detail
        if name == existing.chip and group.name != group.chip:
            name, detail = group.name, group.detail
        merged[position] = replace(
            existing,
            name=name,
            detail=detail,
            chip=existing.chip or group.chip,
            readings=sort_readings(existing.readings + list(group.readings)),
        )
    return merged


def _unique(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
