"""Per-core clock speed and utilisation.

Clocks come from cpufreq, utilisation from the per-CPU rows of /proc/stat.
Both are reported per logical CPU, while a temperature is per physical core,
so threads are folded back onto their core: the clock is the highest of the
core's threads and the load is their mean.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .cputopo import PACKAGE, CpuTopology
from .devinfo import DeviceNamer
from .model import Group, Reading, sort_readings

DEFAULT_PROC_STAT = "/proc/stat"

#: /proc/stat columns that are not the CPU doing work.
_IDLE_FIELDS = (3, 4)  # idle, iowait


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


class _Times:
    __slots__ = ("busy", "total")

    def __init__(self, busy: float, total: float):
        self.busy = busy
        self.total = total


class CpuReader:
    """Stateful reader: utilisation needs the previous /proc/stat to subtract."""

    def __init__(
        self,
        topology: CpuTopology,
        namer: Optional[DeviceNamer] = None,
        proc_stat: str = DEFAULT_PROC_STAT,
    ) -> None:
        self._topology = topology
        self._namer = namer or DeviceNamer()
        self._proc_stat = proc_stat
        self._previous: Dict[str, _Times] = {}

    def read(self) -> Tuple[List[Group], List[str]]:
        topology = self._topology
        if not topology.cores:
            return [], []

        loads = self._cpu_loads()
        name = self._namer.cpu_model()
        groups: List[Group] = []

        for package_id in topology.packages:
            group_key = f"cpu:{package_id}"
            readings: List[Reading] = []

            total = loads.get("cpu")
            if total is not None:
                readings.append(
                    Reading(
                        key=f"{group_key}/load",
                        label="CPU Total Load",
                        kind="load",
                        value=total,
                    )
                )

            for core in topology.cores:
                if core.package_id != package_id:
                    continue
                clock = self._core_clock(core.cpus)
                if clock is not None:
                    readings.append(
                        Reading(
                            key=f"{group_key}/core{core.core_id}/clock",
                            label=f"{core.label} Clock",
                            kind="freq",
                            value=clock,
                            section=core.section,
                        )
                    )
                values = [loads[f"cpu{cpu}"] for cpu in core.cpus if f"cpu{cpu}" in loads]
                if values:
                    readings.append(
                        Reading(
                            key=f"{group_key}/core{core.core_id}/load",
                            label=f"{core.label} Load",
                            kind="load",
                            value=sum(values) / len(values),
                            section=core.section,
                        )
                    )

            if not readings:
                continue

            sections = topology.section_list(package_id)
            core_count = sum(1 for c in topology.cores if c.package_id == package_id)
            thread_count = sum(
                len(c.cpus) for c in topology.cores if c.package_id == package_id
            )
            groups.append(
                Group(
                    key=group_key,
                    name=name or "Processor",
                    detail=f"{core_count} cores / {thread_count} threads",
                    chip="cpu",
                    readings=sort_readings(readings, sections),
                    merge_key=group_key,
                    sections=sections,
                )
            )
        return groups, []

    # -- internals --------------------------------------------------------
    def _core_clock(self, cpus: List[int]) -> Optional[float]:
        """Highest current clock across a core's threads, in MHz."""
        best: Optional[float] = None
        for cpu in cpus:
            base = os.path.join(self._topology.cpu_root, f"cpu{cpu}", "cpufreq")
            # scaling_cur_freq is readable without privileges; cpuinfo_cur_freq
            # often is not.
            raw = _read(os.path.join(base, "scaling_cur_freq"))
            if raw is None:
                raw = _read(os.path.join(base, "cpuinfo_cur_freq"))
            if raw is None:
                continue
            try:
                value = float(raw.strip()) / 1000.0  # kHz -> MHz
            except ValueError:
                continue
            if best is None or value > best:
                best = value
        return best

    def _cpu_loads(self) -> Dict[str, float]:
        """Percentage busy per logical CPU since the previous sample."""
        text = _read(self._proc_stat)
        if text is None:
            return {}

        out: Dict[str, float] = {}
        for line in text.splitlines():
            if not line.startswith("cpu"):
                continue
            fields = line.split()
            name = fields[0]
            try:
                values = [float(v) for v in fields[1:11]]
            except ValueError:
                continue
            if len(values) < 5:
                continue
            total = sum(values)
            idle = sum(values[i] for i in _IDLE_FIELDS if i < len(values))
            current = _Times(busy=total - idle, total=total)

            previous = self._previous.get(name)
            self._previous[name] = current
            if previous is None:
                out[name] = 0.0
                continue
            span = current.total - previous.total
            if span <= 0:
                out[name] = 0.0
                continue
            busy = current.busy - previous.busy
            out[name] = max(0.0, min(100.0, busy / span * 100.0))
        return out


def assign_sections(
    readings: List[Reading], topology: CpuTopology, package_id: int
) -> List[Reading]:
    """Place coretemp channels under the section for their core type.

    "Core 24" becomes "Core 6" in the E-Cores section; anything the topology
    does not recognise, such as AMD's Tctl, is left in the package section.
    """
    from dataclasses import replace

    out: List[Reading] = []
    for reading in readings:
        placed = topology.classify_label(reading.label, package_id)
        if placed is None:
            out.append(replace(reading, section=PACKAGE))
            continue
        section, label = placed
        out.append(replace(reading, section=section, label=label))
    return out
