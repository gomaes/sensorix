"""Friendly device names, disk/NIC throughput and group merging."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwmonitor.sensors.collector import Collector, CollectorConfig  # noqa: E402
from hwmonitor.sensors.devinfo import DeviceNamer  # noqa: E402
from hwmonitor.sensors.disk import DiskReader  # noqa: E402
from hwmonitor.sensors.net import NetReader  # noqa: E402
from hwmonitor.sensors.pciids import PciIds, marketing_name  # noqa: E402
from tests.fake_sysfs import build  # noqa: E402


def _collector(tmp: str, **overrides) -> Collector:
    paths = build(tmp)
    settings = dict(
        hwmon_root=paths["hwmon_root"],
        drm_root=paths["drm_root"],
        net_root=paths["net_root"],
        block_root=paths["block_root"],
        dmi_root=paths["dmi_root"],
        cpuinfo=paths["cpuinfo"],
        pci_ids_paths=(paths["pci_ids"],),
        enable_nvidia=False,
    )
    settings.update(overrides)
    collector = Collector(CollectorConfig(**settings))
    collector.paths = paths  # handy for the tests
    return collector


def _namer(tmp: str) -> DeviceNamer:
    paths = build(tmp)
    return DeviceNamer(
        dmi_root=paths["dmi_root"],
        cpuinfo=paths["cpuinfo"],
        pci_db=PciIds((paths["pci_ids"],)),
    )


def test_pci_ids_prefers_the_subsystem_and_the_marketing_name():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build(tmp)
        db = PciIds((paths["pci_ids"],))
        assert db.available
        # subsystem entry is more specific than the generic device entry
        assert db.lookup("1002", "747e", "1849", "5313") == "Radeon RX 7800 XT"
        assert db.lookup("1002", "747e") == "Radeon RX 7700 XT / 7800 XT"
        assert db.lookup("10ec", "8125") == "RTL8125 2.5GbE Controller"
        assert db.lookup("1002", "ffff") is None
        assert db.lookup("ffff", "0001") is None
        # the class section at the end of the file must not be parsed as vendors
        assert db.lookup("0000", "0000") is None


def test_marketing_name_extraction():
    assert marketing_name("Navi 32 [Radeon RX 7700 XT]") == "Radeon RX 7700 XT"
    assert marketing_name("RTL8125 2.5GbE Controller") == "RTL8125 2.5GbE Controller"
    assert marketing_name("[]") == "[]"


def test_missing_pci_ids_database_is_not_fatal():
    db = PciIds(("/nonexistent/pci.ids",))
    assert db.available is False
    assert db.lookup("1002", "747e") is None


def test_cpu_and_board_names():
    with tempfile.TemporaryDirectory() as tmp:
        namer = _namer(tmp)
        # the "8-Core Processor" suffix is noise
        assert namer.cpu_model() == "AMD Ryzen 7 7800X3D"
        # "COMPUTER INC." is noise too
        assert namer.board_name() == "ASUSTeK PRIME B650-PLUS"


def test_chips_get_model_names():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp)
        groups = {g.chip: g for g in collector.sample().groups}
        assert groups["k10temp"].name == "AMD Ryzen 7 7800X3D"
        assert groups["it8688"].name == "ASUSTeK PRIME B650-PLUS"
        assert groups["nvme"].name == "Samsung SSD 980 PRO 1TB"
        assert groups["amdgpu"].name == "Radeon RX 7800 XT"
        assert groups["drivetemp"].name == "Samsung SSD 870 EVO 1TB"
        assert groups["enp5s0"].name == "RTL8125 2.5GbE Controller"
        # the raw chip name stays available for the "show chip names" toggle
        assert groups["amdgpu"].chip == "amdgpu"


def test_unresolvable_names_fall_back_to_the_chip_name():
    with tempfile.TemporaryDirectory() as tmp:
        # no pci.ids, no DMI, no cpuinfo
        collector = _collector(
            tmp, pci_ids_paths=("/nonexistent",), dmi_root="/nonexistent", cpuinfo="/nonexistent"
        )
        groups = {g.chip: g for g in collector.sample().groups}
        assert groups["k10temp"].name == "k10temp"
        assert groups["amdgpu"].name == "amdgpu"
        assert groups["it8688"].name == "it8688"
        # the model string comes from sysfs, so it survives
        assert groups["nvme"].name == "Samsung SSD 980 PRO 1TB"


def test_disk_and_hwmon_groups_are_merged_into_one_device():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp)
        groups = collector.sample().groups
        names = [g.name for g in groups]
        assert names.count("Samsung SSD 980 PRO 1TB") == 1, names
        assert names.count("Samsung SSD 870 EVO 1TB") == 1, names

        nvme = next(g for g in groups if g.name == "Samsung SSD 980 PRO 1TB")
        labels = [r.label for r in nvme.readings]
        # temperature from hwmon, throughput from /sys/block, in one node
        assert "Composite" in labels
        assert "Read" in labels and "Write" in labels
        assert "Link Speed" in labels
        assert labels.index("Composite") < labels.index("Read")


def test_link_speeds():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp)
        rows = {g.name: {r.label: r for r in g.readings} for g in collector.sample().groups}
        assert rows["Samsung SSD 980 PRO 1TB"]["Link Speed"].text == "16.0 GT/s"
        assert rows["Samsung SSD 870 EVO 1TB"]["Link Speed"].text == "6.0 Gb/s"
        assert rows["RTL8125 2.5GbE Controller"]["Link Speed"].text == "2500 Mb/s"


def test_virtual_devices_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        collector = _collector(tmp)
        chips = {g.chip for g in collector.sample().groups}
        assert "loop0" not in chips      # /sys/block/loop0 has no device link
        assert "lo" not in chips         # loopback interface
        assert "docker0" not in chips    # bridge


def test_disk_rates_are_computed_from_counter_deltas():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build(tmp)
        stat = os.path.join(paths["block_root"], "nvme0n1", "stat")
        reader = DiskReader(paths["block_root"], _namer(tmp))

        reader.read()  # first poll only records the baseline
        # +200000 sectors read (100 MiB), +100000 written (50 MiB), +500 ms busy
        with open(stat, "w") as fh:
            fh.write("  1100    10  2200000  500   900    5  1100000  400   0  12500  900\n")
        # pretend exactly one second passed
        reader._previous["nvme0n1"].timestamp -= 1.0
        groups, _ = reader.read()

        rows = {r.label: r for g in groups if g.chip == "nvme0n1" for r in g.readings}
        assert abs(rows["Read"].value - 200000 * 512 / 1024 / 1024) < 0.5   # ~97.7 MB/s
        assert abs(rows["Write"].value - 100000 * 512 / 1024 / 1024) < 0.5  # ~48.8 MB/s
        assert abs(rows["Activity"].value - 50.0) < 1.0                     # 500 ms of 1 s
        assert rows["Read"].text.endswith("MB/s")


def test_net_rates_are_computed_from_counter_deltas():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build(tmp)
        stats = os.path.join(paths["net_root"], "enp5s0", "statistics")
        reader = NetReader(paths["net_root"], _namer(tmp))

        reader.read()
        with open(os.path.join(stats, "rx_bytes"), "w") as fh:
            fh.write(str(1_000_000_000 + 20 * 1024 * 1024) + "\n")
        with open(os.path.join(stats, "tx_bytes"), "w") as fh:
            fh.write(str(500_000_000 + 5 * 1024 * 1024) + "\n")
        reader._previous["enp5s0"].timestamp -= 2.0
        groups, _ = reader.read()

        rows = {r.label: r for g in groups for r in g.readings}
        assert abs(rows["Download"].value - 10.0) < 0.1   # 20 MiB over 2 s
        assert abs(rows["Upload"].value - 2.5) < 0.1


def test_counter_resets_do_not_produce_negative_rates():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build(tmp)
        stats = os.path.join(paths["net_root"], "enp5s0", "statistics")
        reader = NetReader(paths["net_root"], _namer(tmp))
        reader.read()
        with open(os.path.join(stats, "rx_bytes"), "w") as fh:
            fh.write("0\n")   # interface went down and back up
        reader._previous["enp5s0"].timestamp -= 1.0
        groups, _ = reader.read()
        rows = {r.label: r for g in groups for r in g.readings}
        assert rows["Download"].value == 0.0


def test_missing_roots_are_silently_skipped():
    reader = DiskReader("/nonexistent/block", DeviceNamer())
    assert reader.read() == ([], [])
    assert NetReader("/nonexistent/net", DeviceNamer()).read() == ([], [])


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
