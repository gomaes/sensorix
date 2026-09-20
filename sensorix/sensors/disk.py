"""Throughput, activity and link speed for real block devices.

Rates come from /sys/block/<dev>/stat, whose 3rd and 7th fields count sectors
read and written since boot.  A sector is always 512 bytes here regardless of
the drive's physical sector size, so the deltas between two polls give the
transfer rate directly.

Only devices with a `device` symlink are considered, which keeps loop, ram,
zram, device-mapper and md nodes - none of which are physical hardware - out of
the tree.
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Tuple

from .devinfo import DeviceNamer
from .model import Group, Reading, sort_readings

DEFAULT_BLOCK_ROOT = "/sys/block"

_SECTOR_BYTES = 512
_MB = 1024.0 * 1024.0
_ATA_DIR_RE = re.compile(r"^ata\d+$")


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return None


class _Counters:
    __slots__ = ("read_sectors", "write_sectors", "io_ticks", "timestamp")

    def __init__(self, read_sectors: float, write_sectors: float, io_ticks: float, timestamp: float):
        self.read_sectors = read_sectors
        self.write_sectors = write_sectors
        self.io_ticks = io_ticks
        self.timestamp = timestamp


class DiskReader:
    """Stateful reader: rates need the previous sample to subtract from."""

    def __init__(self, block_root: str = DEFAULT_BLOCK_ROOT, namer: Optional[DeviceNamer] = None):
        self._block_root = block_root
        self._namer = namer or DeviceNamer()
        self._previous: Dict[str, _Counters] = {}

    def read(self) -> Tuple[List[Group], List[str]]:
        if not os.path.isdir(self._block_root):
            return [], []

        groups: List[Group] = []
        try:
            names = sorted(os.listdir(self._block_root))
        except OSError as exc:
            return [], [f"{self._block_root} を読めません: {exc}"]

        now = time.monotonic()
        for name in names:
            block_dir = os.path.join(self._block_root, name)
            device_link = os.path.join(block_dir, "device")
            if not os.path.exists(device_link):
                continue  # virtual device (loop, zram, dm-*, md*)
            group = self._read_device(name, block_dir, device_link, now)
            if group is not None:
                groups.append(group)
        return groups, []

    # -- internals --------------------------------------------------------
    def _read_device(
        self, name: str, block_dir: str, device_link: str, now: float
    ) -> Optional[Group]:
        stat = _read(os.path.join(block_dir, "stat"))
        if not stat:
            return None
        fields = stat.split()
        if len(fields) < 10:
            return None
        try:
            counters = _Counters(
                read_sectors=float(fields[2]),
                write_sectors=float(fields[6]),
                io_ticks=float(fields[9]),
                timestamp=now,
            )
        except ValueError:
            return None

        device_dir = os.path.realpath(device_link)
        group_key = f"disk:{name}"
        model = self._namer.storage_model(device_dir)
        readings: List[Reading] = []

        previous = self._previous.get(name)
        self._previous[name] = counters
        elapsed = counters.timestamp - previous.timestamp if previous else 0.0

        def rate(current: float, before: float) -> float:
            if elapsed <= 0:
                return 0.0
            delta = current - before
            if delta < 0:  # counter wrapped or the device was re-enumerated
                return 0.0
            return delta * _SECTOR_BYTES / _MB / elapsed

        read_rate = rate(counters.read_sectors, previous.read_sectors) if previous else 0.0
        write_rate = rate(counters.write_sectors, previous.write_sectors) if previous else 0.0
        readings.append(
            Reading(key=f"{group_key}/read", label="Read", kind="throughput", value=read_rate)
        )
        readings.append(
            Reading(key=f"{group_key}/write", label="Write", kind="throughput", value=write_rate)
        )

        if previous is not None and elapsed > 0:
            # io_ticks counts milliseconds during which I/O was in flight.
            busy = (counters.io_ticks - previous.io_ticks) / (elapsed * 1000.0) * 100.0
            busy = max(0.0, min(100.0, busy))
        else:
            busy = 0.0
        readings.append(
            Reading(key=f"{group_key}/busy", label="Activity", kind="load", value=busy)
        )

        link = self._link_speed(device_dir)
        if link is not None:
            value, unit = link
            readings.append(
                Reading(
                    key=f"{group_key}/link",
                    label="Link Speed",
                    kind="link",
                    value=value,
                    unit_override=unit,
                    precision_override=1,
                )
            )

        return Group(
            key=group_key,
            name=model or name,
            detail=f"/dev/{name}",
            chip=name,
            readings=sort_readings(readings),
            merge_key=f"dev:{device_dir}",
        )

    def _link_speed(self, device_dir: str) -> Optional[Tuple[float, str]]:
        """SATA link rate in Gb/s, or the NVMe PCIe rate in GT/s."""
        # NVMe: the controller hangs off a PCI device that publishes its rate.
        pci_speed = _read(os.path.join(device_dir, "device", "current_link_speed"))
        if pci_speed:
            match = re.match(r"([\d.]+)\s*GT/s", pci_speed)
            if match:
                return float(match.group(1)), "GT/s"

        # SATA: walk up to the ata port and read the negotiated link rate.
        path = device_dir
        while path and path != "/":
            if _ATA_DIR_RE.match(os.path.basename(path)):
                for link_dir in sorted(_glob_links(path)):
                    raw = _read(os.path.join(link_dir, "sata_spd"))
                    if raw and raw[0].isdigit():
                        match = re.match(r"([\d.]+)", raw)
                        if match:
                            return float(match.group(1)), "Gb/s"
                break
            path = os.path.dirname(path)
        return None


def _glob_links(ata_dir: str) -> List[str]:
    """`.../ata1/link1/ata_link/link1/` holds the negotiated SATA speed."""
    found: List[str] = []
    try:
        for entry in os.listdir(ata_dir):
            if not entry.startswith("link"):
                continue
            ata_link = os.path.join(ata_dir, entry, "ata_link")
            try:
                for sub in os.listdir(ata_link):
                    found.append(os.path.join(ata_link, sub))
            except OSError:
                continue
    except OSError:
        return found
    return found
