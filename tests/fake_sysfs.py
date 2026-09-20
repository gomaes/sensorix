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


def _write_bytes(path: str, value: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(value)


def build(root: str, jitter: bool = False) -> Dict[str, str]:
    """Create a fake sysfs/procfs tree under `root`; returns the roots to use."""
    hwmon_root = os.path.join(root, "class", "hwmon")
    drm_root = os.path.join(root, "class", "drm")
    net_root = os.path.join(root, "class", "net")
    block_root = os.path.join(root, "block")
    devices_root = os.path.join(root, "devices")
    dmi_root = os.path.join(root, "dmi", "id")
    cpuinfo = os.path.join(root, "proc", "cpuinfo")
    pci_ids = os.path.join(root, "share", "pci.ids")

    def noise(scale: int) -> int:
        return random.randint(-scale, scale) if jitter else 0

    # --- backing devices the hwmon/block/net nodes point at ------------
    nvme_device = os.path.join(devices_root, "pci0000:00", "0000:01:00.0", "nvme", "nvme0")
    _write(os.path.join(nvme_device, "model"), "Samsung SSD 980 PRO 1TB")
    _write(os.path.join(nvme_device, "serial"), "S5GXNX0T123456")
    _write(os.path.join(nvme_device, "device", "current_link_speed"), "16.0 GT/s PCIe")
    _write(os.path.join(nvme_device, "device", "current_link_width"), "4")

    ata_port = os.path.join(devices_root, "pci0000:00", "0000:00:17.0", "ata3")
    scsi_device = os.path.join(ata_port, "host2", "target2:0:0", "2:0:0:0")
    _write(os.path.join(scsi_device, "model"), "Samsung SSD 870 EVO 1TB")
    _write(os.path.join(scsi_device, "vendor"), "ATA")
    _write(os.path.join(ata_port, "link3", "ata_link", "link3", "sata_spd"), "6.0 Gbps")

    gpu_pci = os.path.join(devices_root, "pci0000:00", "0000:03:00.0")
    _write(os.path.join(gpu_pci, "vendor"), "0x1002")
    _write(os.path.join(gpu_pci, "device"), "0x747e")
    _write(os.path.join(gpu_pci, "subsystem_vendor"), "0x1849")
    _write(os.path.join(gpu_pci, "subsystem_device"), "0x5313")

    nic_pci = os.path.join(devices_root, "pci0000:00", "0000:05:00.0")
    _write(os.path.join(nic_pci, "vendor"), "0x10ec")
    _write(os.path.join(nic_pci, "device"), "0x8125")

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
    _symlink(nvme_device, os.path.join(nvme, "device"))

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
    _symlink(gpu_pci, os.path.join(amd, "device"))

    card_device = os.path.join(drm_root, "card0", "device")
    _write(os.path.join(card_device, "gpu_busy_percent"), 37 + noise(20))
    _write(os.path.join(card_device, "mem_busy_percent"), 18)
    _write(os.path.join(card_device, "mem_info_vram_used"), 3_221_225_472)
    _write(os.path.join(card_device, "mem_info_vram_total"), 12_884_901_888)
    _symlink(amd, os.path.join(card_device, "hwmon", "hwmon3"))

    # --- hwmon4: a chip with no readable channel at all ---------------
    empty = os.path.join(hwmon_root, "hwmon4")
    _write(os.path.join(empty, "name"), "chip_with_no_channels")

    # --- hwmon5: SATA drive temperature (drivetemp) -------------------
    drivetemp = os.path.join(hwmon_root, "hwmon5")
    _write(os.path.join(drivetemp, "name"), "drivetemp")
    _write(os.path.join(drivetemp, "temp1_label"), "Temperature")
    _write(os.path.join(drivetemp, "temp1_input"), 29000 + noise(4000))
    _write(os.path.join(drivetemp, "temp1_crit"), 70000)
    _symlink(scsi_device, os.path.join(drivetemp, "device"))

    # --- hwmon6-9: DDR5 SPD hubs, one per populated slot --------------
    for index, address in enumerate(("0050", "0051", "0052", "0053")):
        dimm = os.path.join(hwmon_root, f"hwmon{6 + index}")
        _write(os.path.join(dimm, "name"), "spd5118")
        _write(os.path.join(dimm, "temp1_input"), 43500 + index * 500 + noise(1500))
        i2c_device = os.path.join(devices_root, "platform", "i2c-0", f"0-{address}")
        _write(os.path.join(i2c_device, "name"), "spd5118")
        _symlink(i2c_device, os.path.join(dimm, "device"))
    # the first slot has a readable SPD image; the rest are root-only in reality
    spd = bytearray(b"\x00" * 1024)
    part = b"CMK32GX5M2B6000C36".ljust(30, b" ")
    spd[0x209 : 0x209 + 30] = part
    _write_bytes(os.path.join(devices_root, "platform", "i2c-0", "0-0050", "eeprom"), bytes(spd))

    # --- hwmon10: ACPI thermal zone (no vendor, no model) -------------
    acpi = os.path.join(hwmon_root, "hwmon10")
    _write(os.path.join(acpi, "name"), "acpitz")
    _write(os.path.join(acpi, "temp1_input"), 27800 + noise(2000))

    # --- hwmon11: Wi-Fi card whose hwmon hangs off the wiphy ----------
    wifi = os.path.join(hwmon_root, "hwmon11")
    _write(os.path.join(wifi, "name"), "iwlwifi_1")
    _write(os.path.join(wifi, "temp1_input"), 42000 + noise(3000))
    wifi_pci = os.path.join(devices_root, "pci0000:00", "0000:00:14.3")
    _write(os.path.join(wifi_pci, "vendor"), "0x8086")
    _write(os.path.join(wifi_pci, "device"), "0x51f0")
    _write(os.path.join(wifi_pci, "subsystem_vendor"), "0x8086")
    _write(os.path.join(wifi_pci, "subsystem_device"), "0x0094")
    # the hwmon parent is the wiphy, two levels below the PCI function
    wiphy = os.path.join(wifi_pci, "ieee80211", "phy0")
    _write(os.path.join(wiphy, "name"), "phy0")
    _symlink(wiphy, os.path.join(wifi, "device"))

    # --- machine identity, for CPU and mainboard names ----------------
    _write(cpuinfo, "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i5-13600KF\n")
    _write(os.path.join(dmi_root, "board_vendor"), "ASUSTeK COMPUTER INC.")
    _write(os.path.join(dmi_root, "board_name"), "PRIME B650-PLUS")

    # --- a trimmed pci.ids in the real format -------------------------
    _write(pci_ids, PCI_IDS_SAMPLE.strip())

    # --- network interfaces -------------------------------------------
    nic = os.path.join(net_root, "enp5s0")
    _write(os.path.join(nic, "speed"), 2500)
    _write(os.path.join(nic, "operstate"), "up")
    _write(os.path.join(nic, "statistics", "rx_bytes"), 1_000_000_000)
    _write(os.path.join(nic, "statistics", "tx_bytes"), 500_000_000)
    _symlink(nic_pci, os.path.join(nic, "device"))
    # virtual interfaces have no `device` link and must be ignored
    _write(os.path.join(net_root, "lo", "operstate"), "unknown")
    _write(os.path.join(net_root, "docker0", "operstate"), "down")

    # --- block devices -------------------------------------------------
    nvme_block = os.path.join(block_root, "nvme0n1")
    # read I/Os, merges, read sectors, ticks, write I/Os, merges, write sectors,
    # ticks, in flight, io_ticks, time in queue
    _write(os.path.join(nvme_block, "stat"),
           "  1000    10  2000000  500   800    5  1000000  400   0  12000  900")
    _symlink(nvme_device, os.path.join(nvme_block, "device"))

    sata_block = os.path.join(block_root, "sda")
    _write(os.path.join(sata_block, "stat"),
           "   500     5   400000  300   200    2   100000  200   0   4000  500")
    _symlink(scsi_device, os.path.join(sata_block, "device"))

    # loop devices have no `device` link and must be ignored
    _write(os.path.join(block_root, "loop0", "stat"),
           "     0     0        0    0     0    0        0    0   0      0    0")

    return {
        "hwmon_root": hwmon_root,
        "drm_root": drm_root,
        "net_root": net_root,
        "block_root": block_root,
        "dmi_root": dmi_root,
        "cpuinfo": cpuinfo,
        "pci_ids": pci_ids,
    }


def _symlink(target: str, link: str) -> None:
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(target, link)


PCI_IDS_SAMPLE = """
#	Vendor	Device	Subsystem
10ec  Realtek Semiconductor Co., Ltd.
	8125  RTL8125 2.5GbE Controller
1002  Advanced Micro Devices, Inc. [AMD/ATI]
	747e  Navi 32 [Radeon RX 7700 XT / 7800 XT]
		1849 5313  Navi 32 [Radeon RX 7800 XT]
	1638  Cezanne [Radeon Vega Series]
8086  Intel Corporation
	1234  Some Other Device
	51f0  Alder Lake-P PCH CNVi WiFi
		8086 0094  Wi-Fi 6E AX211 160MHz
C 00  Unclassified device
	00  Non-VGA unclassified device
"""


if __name__ == "__main__":  # pragma: no cover - manual helper
    import sys

    dest = sys.argv[1] if len(sys.argv) > 1 else "/tmp/fake-sys"
    paths = build(dest, jitter=True)
    print(
        " ".join(
            [
                f"--hwmon-root {paths['hwmon_root']}",
                f"--drm-root {paths['drm_root']}",
                f"--net-root {paths['net_root']}",
                f"--block-root {paths['block_root']}",
            ]
        )
    )
