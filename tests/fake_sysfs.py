"""Builds a throwaway /sys/class/hwmon look-alike.

Used by the tests, and by `--hwmon-root` when you want to see the GUI on a
machine that has no sensors at all.
"""

from __future__ import annotations

import os
import random
from typing import Dict


def _write(path: str, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f"{value}\n")


def build(root: str, jitter: bool = False) -> Dict[str, str]:
    """Create fake hwmon + drm trees under `root`; returns the two paths."""
    hwmon_root = os.path.join(root, "class", "hwmon")
    drm_root = os.path.join(root, "class", "drm")
    devices_root = os.path.join(root, "devices")

    def noise(scale: int) -> int:
        return random.randint(-scale, scale) if jitter else 0

    # --- hwmon0: AMD CPU package sensor -------------------------------
    k10 = os.path.join(hwmon_root, "hwmon0")
    _write(os.path.join(k10, "name"), "k10temp")
    _write(os.path.join(k10, "temp1_label"), "Tctl")
    _write(os.path.join(k10, "temp1_input"), 45125 + noise(8000))
    _write(os.path.join(k10, "temp1_crit"), 95000)
    _write(os.path.join(k10, "temp2_label"), "Tccd1")
    _write(os.path.join(k10, "temp2_input"), 41250 + noise(8000))
    os.makedirs(os.path.join(devices_root, "pci0000:00", "0000:00:18.3"), exist_ok=True)
    _symlink(os.path.join(devices_root, "pci0000:00", "0000:00:18.3"),
             os.path.join(k10, "device"))

    # --- hwmon1: Super-I/O chip with fans, voltages and PWM -----------
    it87 = os.path.join(hwmon_root, "hwmon1")
    _write(os.path.join(it87, "name"), "it8688")
    _write(os.path.join(it87, "temp1_label"), "System")
    _write(os.path.join(it87, "temp1_input"), 32000 + noise(3000))
    _write(os.path.join(it87, "temp2_input"), 38000 + noise(3000))  # no label on purpose
    _write(os.path.join(it87, "temp2_max"), 80000)
    _write(os.path.join(it87, "fan1_label"), "CPU Fan")
    _write(os.path.join(it87, "fan1_input"), 1180 + noise(240))
    _write(os.path.join(it87, "fan2_input"), 0)
    _write(os.path.join(it87, "in0_label"), "Vcore")
    _write(os.path.join(it87, "in0_input"), 1232 + noise(60))
    _write(os.path.join(it87, "in1_input"), 3376)
    _write(os.path.join(it87, "pwm1"), 128)
    _write(os.path.join(it87, "curr1_input"), 512)
    _write(os.path.join(it87, "power1_input"), 65000000 + noise(9000000))

    # --- hwmon2: NVMe drive, reachable but with an unreadable channel --
    nvme = os.path.join(hwmon_root, "hwmon2")
    _write(os.path.join(nvme, "name"), "nvme")
    _write(os.path.join(nvme, "temp1_label"), "Composite")
    _write(os.path.join(nvme, "temp1_input"), 33850 + noise(6000))
    _write(os.path.join(nvme, "temp1_crit"), 84850)
    _write(os.path.join(nvme, "temp2_input"), "garbage")  # must be skipped, not fatal

    # --- hwmon3: AMD GPU, cross linked from /sys/class/drm/card0 ------
    amd = os.path.join(hwmon_root, "hwmon3")
    _write(os.path.join(amd, "name"), "amdgpu")
    _write(os.path.join(amd, "temp1_label"), "edge")
    _write(os.path.join(amd, "temp1_input"), 52000 + noise(9000))
    _write(os.path.join(amd, "temp2_label"), "junction")
    _write(os.path.join(amd, "temp2_input"), 61000 + noise(9000))
    _write(os.path.join(amd, "temp2_crit"), 110000)
    _write(os.path.join(amd, "fan1_input"), 1450 + noise(300))
    _write(os.path.join(amd, "freq1_input"), 2100000000)
    _write(os.path.join(amd, "power1_input"), 142000000 + noise(20000000))
    _write(os.path.join(amd, "in0_input"), 900)

    card_device = os.path.join(drm_root, "card0", "device")
    _write(os.path.join(card_device, "gpu_busy_percent"), 37 + noise(20))
    _write(os.path.join(card_device, "mem_busy_percent"), 18)
    _write(os.path.join(card_device, "mem_info_vram_used"), 3_221_225_472)
    _write(os.path.join(card_device, "mem_info_vram_total"), 12_884_901_888)
    _symlink(amd, os.path.join(card_device, "hwmon", "hwmon3"))

    # --- hwmon4: a chip with no readable channel at all ---------------
    empty = os.path.join(hwmon_root, "hwmon4")
    _write(os.path.join(empty, "name"), "acpitz")

    return {"hwmon_root": hwmon_root, "drm_root": drm_root}


def _symlink(target: str, link: str) -> None:
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(target, link)


if __name__ == "__main__":  # pragma: no cover - manual helper
    import sys

    dest = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fake-sys"
    paths = build(dest, jitter=True)
    print(f"--hwmon-root {paths['hwmon_root']} --drm-root {paths['drm_root']}")
