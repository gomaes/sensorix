"""Core numbering, core type detection, and per-core clock and load."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sensorix.sensors.collector import Collector, CollectorConfig  # noqa: E402
from sensorix.sensors.cputopo import (  # noqa: E402
    CORE,
    DENSE,
    ECORE,
    LPECORE,
    PACKAGE,
    PCORE,
    CpuTopology,
    parse_cpulist,
)
from tests.fake_sysfs import advance_proc_stat, build_cpu  # noqa: E402


def _topology(tmp: str, layout: str) -> CpuTopology:
    paths = build_cpu(tmp, layout)
    return CpuTopology(paths["cpu_root"], paths["pmu_root"], paths["cpuinfo"])


def _collector(tmp: str, layout: str) -> Collector:
    paths = build_cpu(tmp, layout)
    collector = Collector(
        CollectorConfig(
            hwmon_root=paths["hwmon_root"],
            drm_root="/nonexistent",
            cpu_root=paths["cpu_root"],
            pmu_root=paths["pmu_root"],
            proc_stat=paths["proc_stat"],
            cpuinfo=paths["cpuinfo"],
            dmi_root="/nonexistent",
            pci_ids_paths=("/nonexistent",),
            enable_nvidia=False,
            enable_net=False,
            enable_disk=False,
        )
    )
    collector.paths = paths
    return collector


def _by_section(group):
    out = {}
    for reading in group.readings:
        out.setdefault(reading.section, []).append(reading)
    return out


def test_parse_cpulist():
    assert parse_cpulist("0-3,8,12-13") == [0, 1, 2, 3, 8, 12, 13]
    assert parse_cpulist("") == []
    assert parse_cpulist("bogus") == []


def test_sparse_topology_core_ids_become_sequential_numbers():
    """coretemp calls them Core 0, 4, 8 ... 24, 25; that is not a core index."""
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "intel_hybrid")
        p_cores = [c for c in topology.cores if c.type_key == PCORE]
        e_cores = [c for c in topology.cores if c.type_key == ECORE]

        assert [c.core_id for c in p_cores] == [0, 4, 8, 12, 16, 20]
        assert [c.label for c in p_cores] == [f"Core {i}" for i in range(6)]
        assert [c.core_id for c in e_cores] == list(range(24, 32))
        assert [c.label for c in e_cores] == [f"Core {i}" for i in range(8)]

        # both threads of a P-core map back to the same physical core
        assert topology.by_cpu[0] is topology.by_cpu[1]
        assert topology.by_cpu[0].cpus == [0, 1]
        assert topology.by_cpu[12].cpus == [12]


def test_core_types_from_the_kernel_types_directory():
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "intel_hybrid")
        assert topology.hybrid is True
        kinds = {}
        for core in topology.cores:
            kinds[core.type_key] = kinds.get(core.type_key, 0) + 1
        assert kinds == {PCORE: 6, ECORE: 8}


def test_core_types_fall_back_to_the_hybrid_pmus():
    """Kernels before 6.13 have no cpu/types/, only cpu_core and cpu_atom."""
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "intel_pmu")
        assert not os.path.exists(os.path.join(topology.cpu_root, "types"))
        assert topology.hybrid is True
        assert sum(1 for c in topology.cores if c.type_key == PCORE) == 6
        assert sum(1 for c in topology.cores if c.type_key == ECORE) == 8


def test_low_power_e_cores_are_split_off_by_speed():
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "intel_lpe")
        kinds = {}
        for core in topology.cores:
            kinds[core.type_key] = kinds.get(core.type_key, 0) + 1
        assert kinds == {PCORE: 6, ECORE: 8, LPECORE: 2}


def test_amd_dense_cores_are_detected_from_their_lower_ceiling():
    """AMD publishes no core type, so Zen*c cores show up as a speed tier."""
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "amd_dense")
        kinds = {}
        for core in topology.cores:
            kinds[core.type_key] = kinds.get(core.type_key, 0) + 1
        assert kinds == {CORE: 4, DENSE: 8}
        assert topology.section_list() == (
            (CORE, "Cores"),
            (DENSE, "Dense Cores"),
            (PACKAGE, "Package"),
        )


def test_a_uniform_cpu_is_never_split():
    with tempfile.TemporaryDirectory() as tmp:
        topology = _topology(tmp, "uniform")
        assert topology.hybrid is False
        assert {c.type_key for c in topology.cores} == {CORE}
        assert topology.section_list() == ((CORE, "Cores"), (PACKAGE, "Package"))


def test_boost_differences_are_not_mistaken_for_core_types():
    """AMD's preferred core ranking gives each core a slightly different
    ceiling; that must not look like a hybrid layout."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = build_cpu(tmp, "uniform")
        for cpu, khz in enumerate((3000000, 2980000, 2950000, 2930000)):
            with open(os.path.join(paths["cpu_root"], f"cpu{cpu}",
                                   "cpufreq", "cpuinfo_max_freq"), "w") as fh:
                fh.write(f"{khz}\n")
        topology = CpuTopology(paths["cpu_root"], paths["pmu_root"], paths["cpuinfo"])
        assert topology.hybrid is False


def test_temperatures_are_placed_under_their_core_type():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp, "intel_hybrid")
        group = next(g for g in collector.sample().groups if g.chip == "coretemp")

        assert group.name == "13th Gen Intel Core i5-13600KF"
        assert group.sections == (
            (PCORE, "P-Cores"),
            (ECORE, "E-Cores"),
            (PACKAGE, "Package"),
        )
        sections = _by_section(group)
        temps = lambda key: [r.label for r in sections[key] if r.kind == "temp"]  # noqa: E731
        assert temps(PCORE) == [f"Core {i}" for i in range(6)]
        assert temps(ECORE) == [f"Core {i}" for i in range(8)]
        assert temps(PACKAGE) == ["Package"]
        # the overall load is a group summary, not part of any section
        assert [r.label for r in sections[None]] == ["CPU Total Load"]


def test_clock_and_load_rows_exist_for_every_core():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp, "intel_hybrid")
        collector.sample()
        advance_proc_stat(collector.paths["proc_stat"], busy=30, idle=70)
        group = next(g for g in collector.sample().groups if g.chip == "coretemp")
        sections = _by_section(group)

        p_rows = {r.label: r for r in sections[PCORE]}
        assert p_rows["Core 0 Clock"].text == "4900 MHz"
        assert p_rows["Core 5 Clock"].text == "4900 MHz"
        e_rows = {r.label: r for r in sections[ECORE]}
        assert e_rows["Core 0 Clock"].text == "3700 MHz"

        # 30 busy ticks out of 100 added
        assert abs(p_rows["Core 0 Load"].value - 30.0) < 0.01
        assert p_rows["Core 0 Load"].text == "30 %"
        assert len([r for r in sections[PCORE] if r.kind == "load"]) == 6
        assert len([r for r in sections[ECORE] if r.kind == "load"]) == 8


def test_first_sample_has_no_load_delta_to_work_from():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp, "intel_hybrid")
        group = next(g for g in collector.sample().groups if g.chip == "coretemp")
        loads = [r for r in group.readings if r.kind == "load"]
        assert loads and all(r.value == 0.0 for r in loads)


def test_unrecognised_temperature_labels_land_in_the_package_section():
    """AMD's k10temp reports Tctl and Tccd1, which are not per-core."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = build_cpu(tmp, "uniform")
        hwmon = os.path.join(paths["hwmon_root"], "hwmon0")
        with open(os.path.join(hwmon, "name"), "w") as fh:
            fh.write("k10temp\n")
        with open(os.path.join(hwmon, "temp1_label"), "w") as fh:
            fh.write("Tctl\n")
        with open(os.path.join(hwmon, "temp2_label"), "w") as fh:
            fh.write("Tccd1\n")

        collector = Collector(
            CollectorConfig(
                hwmon_root=paths["hwmon_root"],
                drm_root="/nonexistent",
                cpu_root=paths["cpu_root"],
                pmu_root=paths["pmu_root"],
                proc_stat=paths["proc_stat"],
                cpuinfo=paths["cpuinfo"],
                dmi_root="/nonexistent",
                pci_ids_paths=("/nonexistent",),
                enable_nvidia=False,
                enable_net=False,
                enable_disk=False,
            )
        )
        group = next(g for g in collector.sample().groups if g.chip == "k10temp")
        sections = _by_section(group)
        package = [r.label for r in sections[PACKAGE] if r.kind == "temp"]
        assert "Tctl" in package and "Tccd1" in package
        # per-core clocks and loads are still grouped properly
        assert len([r for r in sections[CORE] if r.kind == "freq"]) == 4


def test_cpu_backend_can_be_turned_off():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build_cpu(tmp, "intel_hybrid")
        collector = Collector(
            CollectorConfig(
                hwmon_root=paths["hwmon_root"],
                drm_root="/nonexistent",
                cpu_root=paths["cpu_root"],
                pmu_root=paths["pmu_root"],
                proc_stat=paths["proc_stat"],
                cpuinfo=paths["cpuinfo"],
                dmi_root="/nonexistent",
                pci_ids_paths=("/nonexistent",),
                enable_nvidia=False,
                enable_net=False,
                enable_disk=False,
                enable_cpu=False,
            )
        )
        group = next(g for g in collector.sample().groups if g.chip == "coretemp")
        assert group.sections == ()
        # the raw topology labels come back unchanged
        assert "Core 24" in [r.label for r in group.readings]
        assert all(r.section is None for r in group.readings)


def test_missing_cpu_root_is_not_fatal():
    topology = CpuTopology("/nonexistent/cpu", "/nonexistent", "/nonexistent")
    assert topology.cores == []
    assert topology.section_list() == ((PACKAGE, "Package"),)


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  ok  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
