"""Backend tests.  Run with `pytest`, or directly with `python tests/test_sensors.py`."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwmonitor.sensors import lmsensors  # noqa: E402
from hwmonitor.sensors.collector import Collector, CollectorConfig  # noqa: E402
from hwmonitor.sensors.hwmon import read_hwmon  # noqa: E402
from hwmonitor.sensors.model import StatsStore  # noqa: E402
from tests.fake_sysfs import build  # noqa: E402


def _fixture(tmp: str):
    paths = build(tmp)
    return read_hwmon(paths["hwmon_root"], paths["drm_root"])


def test_hwmon_groups_and_scaling():
    with tempfile.TemporaryDirectory() as tmp:
        groups, errors = _fixture(tmp)
        assert errors == [], errors
        by_name = {g.name: g for g in groups}
        assert set(by_name) == {"k10temp", "it8688", "nvme", "amdgpu"}, sorted(by_name)

        # a chip without a single readable channel is dropped, not reported
        assert "acpitz" not in by_name

        k10 = by_name["k10temp"]
        tctl = next(r for r in k10.readings if r.label == "Tctl")
        assert abs(tctl.value - 45.125) < 1e-6     # millidegrees -> degrees
        assert tctl.limit == 95.0                  # temp1_crit
        assert tctl.text == "45.1 °C"

        it87 = by_name["it8688"]
        labels = [r.label for r in it87.readings]
        # temps first, then fans, pwm, voltages, currents, power
        assert labels[0] == "System" and labels[1] == "Temp 2"
        assert "CPU Fan" in labels and "Fan 2" in labels
        values = {r.label: r for r in it87.readings}
        assert values["Vcore"].text == "1.232 V"          # millivolts -> volts
        assert values["CPU Fan"].text == "1180 RPM"
        assert values["PWM 1"].text == "50 %"             # 128/255
        assert values["Power 1"].text == "65.00 W"        # microwatts -> watts
        assert values["Current 1"].text == "0.512 A"
        assert values["Temp 2"].limit == 80.0             # falls back to temp*_max

        nvme = by_name["nvme"]
        # temp2_input contains junk -> that one channel is skipped silently
        assert [r.label for r in nvme.readings] == ["Composite"]


def test_gpu_group_is_annotated_and_extended():
    with tempfile.TemporaryDirectory() as tmp:
        groups, _ = _fixture(tmp)
        gpu = next(g for g in groups if g.name == "amdgpu")
        assert "card0" in gpu.detail
        rows = {r.label: r for r in gpu.readings}
        assert rows["GPU Load"].text == "37 %"
        assert rows["Memory Used"].text == "3072 MB"
        assert rows["Memory Usage"].text == "25 %"
        assert rows["Clock 1"].text == "2100 MHz"          # Hz -> MHz
        assert rows["junction"].is_alarm(80.0) is False
        assert rows["junction"].value == 61.0


def test_alarm_uses_the_lower_of_threshold_and_hardware_limit():
    with tempfile.TemporaryDirectory() as tmp:
        groups, _ = _fixture(tmp)
        rows = {r.label: r for g in groups for r in g.readings}
        composite = rows["Composite"]        # 33.85 °C, crit 84.85
        assert composite.is_alarm(80.0) is False
        assert composite.is_alarm(30.0) is True
        assert rows["CPU Fan"].is_alarm(1.0) is False   # only temps can alarm


def test_missing_hwmon_root_is_reported_not_raised():
    groups, errors = read_hwmon("/nonexistent/hwmon", "/nonexistent/drm")
    assert groups == []
    assert errors and "見つかりません" in errors[0]


def test_stats_track_min_max_avg():
    store = StatsStore()
    for value in (40.0, 50.0, 30.0):
        stats = store.update("k", value)
    assert (stats.minimum, stats.maximum, stats.count) == (30.0, 50.0, 3)
    assert abs(stats.average - 40.0) < 1e-9
    store.reset()
    assert store.get("k") is None


def test_collector_attaches_stats_and_survives_a_broken_backend():
    with tempfile.TemporaryDirectory() as tmp:
        paths = build(tmp)
        collector = Collector(
            CollectorConfig(
                hwmon_root=paths["hwmon_root"],
                drm_root=paths["drm_root"],
                enable_nvidia=False,
            )
        )
        first = collector.sample()
        assert first.sensor_count > 15
        assert first.errors == []
        assert first.sources and first.sources[0].startswith("hwmon")
        second = collector.sample()
        reading = second.groups[0].readings[0]
        assert reading.stats is not None and reading.stats.count == 2

        collector.reset_stats()
        third = collector.sample()
        assert third.groups[0].readings[0].stats.count == 1

        # a backend that explodes must degrade into an error string
        import hwmonitor.sensors.collector as collector_module

        def boom(*_args, **_kwargs):
            raise RuntimeError("kaboom")

        original = collector_module.hwmon.read_hwmon
        collector_module.hwmon.read_hwmon = boom
        try:
            broken = collector.sample()
        finally:
            collector_module.hwmon.read_hwmon = original
        assert broken.groups == []
        assert any("kaboom" in e for e in broken.errors)


def test_lmsensors_parsing():
    payload = {
        "k10temp-pci-00c3": {
            "Adapter": "PCI adapter",
            "Tctl": {"temp1_input": 41.5, "temp1_crit": 95.0},
            "Tccd1": {"temp3_input": 38.75},
        },
        "it8688-isa-0a40": {
            "Adapter": "ISA adapter",
            "in0": {"in0_input": 1.232},
            "fan1": {"fan1_input": 1180.0},
            "pwm1": {"pwm1": 255.0},
        },
    }
    groups = {g.name: g for g in lmsensors.parse(payload)}
    assert set(groups) == {"k10temp", "it8688"}     # bus suffix stripped
    assert groups["k10temp"].detail == "PCI adapter"
    tctl = next(r for r in groups["k10temp"].readings if r.label == "Tctl")
    assert tctl.text == "41.5 °C" and tctl.limit == 95.0
    it = {r.label: r for r in groups["it8688"].readings}
    assert it["fan1"].text == "1180 RPM"
    assert it["in0"].text == "1.232 V"
    assert it["pwm1"].text == "100 %"


def test_lmsensors_tolerates_trailing_commas():
    text = '{"a-isa-0000": {"Adapter": "ISA adapter", "t": {"temp1_input": 1.0,},},}'
    assert lmsensors._loads_tolerant(text)["a-isa-0000"]["t"]["temp1_input"] == 1.0


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
