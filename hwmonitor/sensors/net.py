"""Link speed and live throughput for physical network interfaces.

/sys/class/net/<if>/statistics/{rx,tx}_bytes are monotonic byte counters, so
the difference between two polls is the transfer rate.  Interfaces without a
`device` symlink are virtual (lo, docker0, veth*, br*, tun*, wg*) and skipped.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

from .devinfo import DeviceNamer
from .model import Group, Reading, sort_readings

DEFAULT_NET_ROOT = "/sys/class/net"

_MB = 1024.0 * 1024.0


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        # `speed` raises EINVAL for a down link or a Wi-Fi adapter.
        return None


def _read_float(path: str) -> Optional[float]:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class _Counters:
    __slots__ = ("rx", "tx", "timestamp")

    def __init__(self, rx: float, tx: float, timestamp: float):
        self.rx = rx
        self.tx = tx
        self.timestamp = timestamp


class NetReader:
    """Stateful reader: rates need the previous sample to subtract from."""

    def __init__(self, net_root: str = DEFAULT_NET_ROOT, namer: Optional[DeviceNamer] = None):
        self._net_root = net_root
        self._namer = namer or DeviceNamer()
        self._previous: Dict[str, _Counters] = {}

    def read(self) -> Tuple[List[Group], List[str]]:
        if not os.path.isdir(self._net_root):
            return [], []

        try:
            names = sorted(os.listdir(self._net_root))
        except OSError as exc:
            return [], [f"{self._net_root} を読めません: {exc}"]

        now = time.monotonic()
        groups: List[Group] = []
        for name in names:
            iface_dir = os.path.join(self._net_root, name)
            if not os.path.exists(os.path.join(iface_dir, "device")):
                continue  # loopback, bridges, containers, VPN tunnels
            group = self._read_iface(name, iface_dir, now)
            if group is not None:
                groups.append(group)
        return groups, []

    def _read_iface(self, name: str, iface_dir: str, now: float) -> Optional[Group]:
        stats_dir = os.path.join(iface_dir, "statistics")
        rx = _read_float(os.path.join(stats_dir, "rx_bytes"))
        tx = _read_float(os.path.join(stats_dir, "tx_bytes"))
        if rx is None or tx is None:
            return None

        counters = _Counters(rx, tx, now)
        previous = self._previous.get(name)
        self._previous[name] = counters
        elapsed = counters.timestamp - previous.timestamp if previous else 0.0

        def rate(current: float, before: float) -> float:
            if elapsed <= 0:
                return 0.0
            delta = current - before
            if delta < 0:  # counters reset when the interface goes down
                return 0.0
            return delta / _MB / elapsed

        group_key = f"net:{name}"
        readings = [
            Reading(
                key=f"{group_key}/rx",
                label="Download",
                kind="throughput",
                value=rate(counters.rx, previous.rx) if previous else 0.0,
            ),
            Reading(
                key=f"{group_key}/tx",
                label="Upload",
                kind="throughput",
                value=rate(counters.tx, previous.tx) if previous else 0.0,
            ),
        ]

        speed = _read_float(os.path.join(iface_dir, "speed"))
        if speed is not None and speed > 0:
            readings.append(
                Reading(key=f"{group_key}/speed", label="Link Speed", kind="link", value=speed)
            )

        device_dir = os.path.realpath(os.path.join(iface_dir, "device"))
        model = self._namer.pci_name(device_dir)
        state = _read(os.path.join(iface_dir, "operstate")) or "unknown"
        detail = f"{name} · {state}"

        return Group(
            key=group_key,
            name=model or name,
            detail=detail,
            chip=name,
            readings=sort_readings(readings),
        )
