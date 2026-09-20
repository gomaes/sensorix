"""Works out which physical core each CPU belongs to, and what kind it is.

Two things are needed to show a modern CPU sensibly:

* `coretemp` labels its channels "Core 24", where 24 is the topology core id.
  On a hybrid part those ids are sparse (0, 4, 8 ... for the P-cores, then
  24, 25, 26 ... for the E-cores), which reads like something is missing.
  Mapping the id back to a core lets each core be numbered from 0 within its
  own kind.
* Hybrid parts mix core types, and lumping a 5.1 GHz P-core in with a 3.9 GHz
  E-core in one list hides the thing you are usually looking for.

Core types are taken from the most authoritative source available; see
`_detect_types` for the order.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_CPU_ROOT = "/sys/devices/system/cpu"
DEFAULT_PMU_ROOT = "/sys/devices"
DEFAULT_CPUINFO = "/proc/cpuinfo"

#: Section keys, in the order they should appear under the CPU group.
PCORE = "pcore"
ECORE = "ecore"
LPECORE = "lpecore"
CORE = "core"
DENSE = "dense"
PACKAGE = "package"

SECTION_NAMES = {
    PCORE: "P-Cores",
    ECORE: "E-Cores",
    LPECORE: "LPE-Cores",
    CORE: "Cores",
    DENSE: "Dense Cores",
    PACKAGE: "Package",
}
SECTION_ORDER = (PCORE, ECORE, LPECORE, CORE, DENSE, PACKAGE)

#: Two max frequencies are the same tier when within this ratio of each other.
_TIER_TOLERANCE = 0.05
#: AMD dense cores clock materially lower; anything closer is one tier.
_TIER_SPLIT_RATIO = 0.85

_CPU_DIR_RE = re.compile(r"^cpu(\d+)$")
_CORETEMP_CORE_RE = re.compile(r"^Core (\d+)$")
_CORETEMP_PACKAGE_RE = re.compile(r"^Package id (\d+)$")


def parse_cpulist(text: str) -> List[int]:
    """"0-3,8,12-13" -> [0, 1, 2, 3, 8, 12, 13]"""
    out: List[int] = []
    for part in (text or "").strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                out.extend(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            value = fh.read().strip()
    except (OSError, ValueError):
        return None
    return value or None


def _read_int(path: str) -> Optional[int]:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass
class Core:
    """One physical core, with the logical CPUs (threads) it runs."""

    core_id: int
    package_id: int
    type_key: str
    cpus: List[int] = field(default_factory=list)
    #: Position within its own type, counted from 0 - what gets displayed.
    index: int = 0

    @property
    def section(self) -> str:
        return self.type_key

    @property
    def label(self) -> str:
        return f"Core {self.index}"


class CpuTopology:
    """Snapshot of core layout; the kernel does not change it at runtime."""

    def __init__(
        self,
        cpu_root: str = DEFAULT_CPU_ROOT,
        pmu_root: str = DEFAULT_PMU_ROOT,
        cpuinfo: str = DEFAULT_CPUINFO,
    ) -> None:
        self.cpu_root = cpu_root
        self.pmu_root = pmu_root
        self.cpuinfo = cpuinfo
        self.cores: List[Core] = []
        self.by_core_id: Dict[Tuple[int, int], Core] = {}
        self.by_cpu: Dict[int, Core] = {}
        self.packages: List[int] = []
        self.hybrid = False
        self._build()

    # -- construction -----------------------------------------------------
    def _cpu_ids(self) -> List[int]:
        try:
            entries = os.listdir(self.cpu_root)
        except OSError:
            return []
        ids = []
        for entry in entries:
            match = _CPU_DIR_RE.match(entry)
            if match and os.path.isdir(os.path.join(self.cpu_root, entry, "topology")):
                ids.append(int(match.group(1)))
        return sorted(ids)

    def _build(self) -> None:
        cpu_ids = self._cpu_ids()
        if not cpu_ids:
            return

        types = self._detect_types(cpu_ids)
        self.hybrid = len({key for key in types.values()}) > 1

        cores: Dict[Tuple[int, int], Core] = {}
        for cpu in cpu_ids:
            topo = os.path.join(self.cpu_root, f"cpu{cpu}", "topology")
            core_id = _read_int(os.path.join(topo, "core_id"))
            package_id = _read_int(os.path.join(topo, "physical_package_id"))
            if core_id is None:
                core_id = cpu
            if package_id is None:
                package_id = 0
            key = (package_id, core_id)
            core = cores.get(key)
            if core is None:
                core = Core(
                    core_id=core_id,
                    package_id=package_id,
                    type_key=types.get(cpu, CORE),
                    cpus=[],
                )
                cores[key] = core
            core.cpus.append(cpu)
            self.by_cpu[cpu] = core

        # Number each core from 0 within its own type, in CPU order, so the
        # display reads "Core 0..5" per kind instead of the sparse topology ids.
        ordered = sorted(cores.values(), key=lambda c: (c.package_id, min(c.cpus)))
        counters: Dict[Tuple[int, str], int] = {}
        for core in ordered:
            counter_key = (core.package_id, core.type_key)
            core.index = counters.get(counter_key, 0)
            counters[counter_key] = core.index + 1

        self.cores = ordered
        self.by_core_id = cores
        self.packages = sorted({core.package_id for core in ordered})

    # -- core type detection ----------------------------------------------
    def _detect_types(self, cpu_ids: Sequence[int]) -> Dict[int, str]:
        for detector in (self._types_from_sysfs, self._types_from_pmu, self._types_from_speed):
            found = detector(cpu_ids)
            if found:
                return found
        return {cpu: CORE for cpu in cpu_ids}

    def _types_from_sysfs(self, cpu_ids: Sequence[int]) -> Dict[int, str]:
        """Linux 6.13+ publishes hybrid core types under cpu/types/."""
        root = os.path.join(self.cpu_root, "types")
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return {}

        groups: List[Tuple[str, List[int]]] = []
        for entry in entries:
            cpulist = _read(os.path.join(root, entry, "cpulist"))
            if not cpulist:
                continue
            cpus = [cpu for cpu in parse_cpulist(cpulist) if cpu in set(cpu_ids)]
            if cpus:
                groups.append((entry, cpus))
        if not groups:
            return {}

        performance = [(name, cpus) for name, cpus in groups if "core" in name]
        efficiency = [(name, cpus) for name, cpus in groups if "atom" in name]
        others = [g for g in groups if g not in performance and g not in efficiency]
        if not performance and not efficiency:
            # Unknown naming scheme; fall back to frequency tiers.
            return {}

        types: Dict[int, str] = {}
        for _, cpus in performance:
            for cpu in cpus:
                types[cpu] = PCORE
        for key, cpus in self._rank_efficiency(efficiency):
            for cpu in cpus:
                types[cpu] = key
        for _, cpus in others:
            for cpu in cpus:
                types.setdefault(cpu, CORE)
        return types

    def _types_from_pmu(self, cpu_ids: Sequence[int]) -> Dict[int, str]:
        """Alder Lake onwards expose hybrid PMUs as cpu_core / cpu_atom."""
        performance = _read(os.path.join(self.pmu_root, "cpu_core", "cpus"))
        efficiency = _read(os.path.join(self.pmu_root, "cpu_atom", "cpus"))
        if not performance or not efficiency:
            return {}

        valid = set(cpu_ids)
        types: Dict[int, str] = {}
        for cpu in parse_cpulist(performance):
            if cpu in valid:
                types[cpu] = PCORE
        atoms = [cpu for cpu in parse_cpulist(efficiency) if cpu in valid]
        if not types or not atoms:
            return {}

        # Meteor/Lunar Lake put low power E-cores on the SoC tile: same PMU,
        # lower ceiling. Split them out when the speeds say two tiers.
        tiers = self._frequency_tiers(atoms)
        if len(tiers) == 2:
            for cpu in tiers[0]:
                types[cpu] = ECORE
            for cpu in tiers[1]:
                types[cpu] = LPECORE
        else:
            for cpu in atoms:
                types[cpu] = ECORE
        return types

    def _types_from_speed(self, cpu_ids: Sequence[int]) -> Dict[int, str]:
        """Last resort, and the only option for AMD's dense (c) cores.

        AMD does not publish a core type anywhere, but Zen4c/Zen5c cores carry a
        visibly lower maximum frequency than the classic cores beside them.
        """
        tiers = self._frequency_tiers(list(cpu_ids))
        if len(tiers) != 2:
            return {}
        fast, slow = tiers
        if len(fast) < 2 or len(slow) < 2:
            return {}
        return {
            **{cpu: CORE for cpu in fast},
            **{cpu: DENSE for cpu in slow},
        }

    def _rank_efficiency(self, groups) -> List[Tuple[str, List[int]]]:
        """Fastest efficiency group is "E", anything slower is "LPE"."""
        if not groups:
            return []
        if len(groups) == 1:
            return [(ECORE, groups[0][1])]
        ranked = sorted(
            groups, key=lambda item: self._max_frequency(item[1]) or 0, reverse=True
        )
        out = [(ECORE, ranked[0][1])]
        for _, cpus in ranked[1:]:
            out.append((LPECORE, cpus))
        return out

    def _max_frequency(self, cpus: Sequence[int]) -> Optional[int]:
        values = [v for v in (self._cpu_max_frequency(cpu) for cpu in cpus) if v]
        return max(values) if values else None

    def _cpu_max_frequency(self, cpu: int) -> Optional[int]:
        base = os.path.join(self.cpu_root, f"cpu{cpu}")
        for relative in (
            os.path.join("cpufreq", "cpuinfo_max_freq"),
            os.path.join("acpi_cppc", "highest_perf"),
        ):
            value = _read_int(os.path.join(base, relative))
            if value:
                return value
        return None

    def _frequency_tiers(self, cpus: Sequence[int]) -> List[List[int]]:
        """Split CPUs into speed tiers, fastest first.

        Returns [] unless there are exactly two well separated tiers, so that
        per-core boost differences - AMD's preferred core ranking in
        particular - are not mistaken for a hybrid layout.
        """
        speeds = {cpu: self._cpu_max_frequency(cpu) for cpu in cpus}
        speeds = {cpu: value for cpu, value in speeds.items() if value}
        if len(speeds) < 4:
            return []

        clusters: List[List[int]] = []
        for cpu, value in sorted(speeds.items(), key=lambda kv: -kv[1]):
            for cluster in clusters:
                reference = speeds[cluster[0]]
                if abs(value - reference) <= reference * _TIER_TOLERANCE:
                    cluster.append(cpu)
                    break
            else:
                clusters.append([cpu])

        if len(clusters) != 2:
            return []
        fast, slow = clusters
        if speeds[slow[0]] > speeds[fast[0]] * _TIER_SPLIT_RATIO:
            return []  # too close together to be different core types
        return [sorted(fast), sorted(slow)]

    # -- lookups used by the backends -------------------------------------
    def section_list(self, package_id: Optional[int] = None) -> Tuple[Tuple[str, str], ...]:
        """Sections this CPU needs, in display order."""
        present = {
            core.type_key
            for core in self.cores
            if package_id is None or core.package_id == package_id
        }
        present.add(PACKAGE)
        return tuple((key, SECTION_NAMES[key]) for key in SECTION_ORDER if key in present)

    def classify_label(self, label: str, package_id: int = 0):
        """Map a coretemp channel label onto a section and a display name.

        Returns (section key, display label) or None when the label is not one
        of coretemp's, e.g. AMD's Tctl.
        """
        match = _CORETEMP_CORE_RE.match(label)
        if match is not None:
            core = self.by_core_id.get((package_id, int(match.group(1))))
            if core is not None:
                return core.section, core.label
            return None
        if _CORETEMP_PACKAGE_RE.match(label) is not None:
            return PACKAGE, "Package"
        return None
