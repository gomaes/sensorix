"""Resolve PCI vendor/device IDs into human readable model names.

Arch ships the database as /usr/share/hwdata/pci.ids (the `hwdata` package,
pulled in by pciutils among others).  Only the vendors we actually have on the
machine are parsed, and the result is cached, so this costs a couple of
milliseconds once at start-up rather than holding the whole 1.5 MB file in
memory.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

SEARCH_PATHS = (
    "/usr/share/hwdata/pci.ids",
    "/usr/share/misc/pci.ids",
    "/usr/share/pci.ids",
    "/usr/local/share/hwdata/pci.ids",
)

_VENDOR_RE = re.compile(r"^([0-9a-f]{4})\s+(.+)$")
_DEVICE_RE = re.compile(r"^\t([0-9a-f]{4})\s+(.+)$")
_SUBSYS_RE = re.compile(r"^\t\t([0-9a-f]{4})\s+([0-9a-f]{4})\s+(.+)$")


def marketing_name(name: str) -> str:
    """Pull the consumer-facing name out of a pci.ids description.

    pci.ids spells GPUs as "Navi 32 [Radeon RX 7700 XT / 7800 XT]": the codename
    first, the name people actually recognise in brackets.
    """
    start = name.rfind("[")
    end = name.rfind("]")
    if 0 <= start < end:
        inner = name[start + 1 : end].strip()
        if inner:
            return inner
    return name.strip()


class PciIds:
    """Lazy, per-vendor reader for the pci.ids database."""

    def __init__(self, search_paths=SEARCH_PATHS) -> None:
        self._search_paths = tuple(search_paths)
        self._path: Optional[str] = None
        self._resolved_path = False
        # vendor id -> {device id -> (name, {(subvendor, subdevice): name})}
        self._cache: Dict[str, Dict[str, Tuple[str, Dict[Tuple[str, str], str]]]] = {}

    @property
    def path(self) -> Optional[str]:
        if not self._resolved_path:
            self._resolved_path = True
            for candidate in self._search_paths:
                if os.path.isfile(candidate):
                    self._path = candidate
                    break
        return self._path

    @property
    def available(self) -> bool:
        return self.path is not None

    def _vendor(self, vendor: str):
        if vendor in self._cache:
            return self._cache[vendor]

        devices: Dict[str, Tuple[str, Dict[Tuple[str, str], str]]] = {}
        path = self.path
        if path is None:
            self._cache[vendor] = devices
            return devices

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                in_vendor = False
                current: Optional[str] = None
                for line in fh:
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("C "):
                        break  # device classes follow the vendor list
                    if not line.startswith("\t"):
                        match = _VENDOR_RE.match(line.rstrip("\n"))
                        if match is None:
                            continue
                        if in_vendor:
                            break  # we already collected our vendor
                        in_vendor = match.group(1) == vendor
                        current = None
                        continue
                    if not in_vendor:
                        continue
                    sub = _SUBSYS_RE.match(line.rstrip("\n"))
                    if sub is not None:
                        if current is not None:
                            devices[current][1][(sub.group(1), sub.group(2))] = sub.group(3)
                        continue
                    dev = _DEVICE_RE.match(line.rstrip("\n"))
                    if dev is not None:
                        current = dev.group(1)
                        devices[current] = (dev.group(2), {})
        except OSError:
            pass

        self._cache[vendor] = devices
        return devices

    def lookup(
        self,
        vendor: str,
        device: str,
        subvendor: Optional[str] = None,
        subdevice: Optional[str] = None,
    ) -> Optional[str]:
        """Return the best name for a device, preferring the subsystem entry."""
        vendor, device = vendor.lower(), device.lower()
        entry = self._vendor(vendor).get(device)
        if entry is None:
            return None
        name, subsystems = entry
        if subvendor and subdevice:
            specific = subsystems.get((subvendor.lower(), subdevice.lower()))
            if specific:
                return marketing_name(specific)
        return marketing_name(name)


#: Shared instance; the parse cost is paid once per vendor per process.
DEFAULT = PciIds()
