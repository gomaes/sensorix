"""Turn kernel driver names into names people recognise.

`nvme` and `amdgpu` say nothing about which drive or card is being monitored,
so every group is given a model name pulled from sysfs: the NVMe/ATA model
string, the AMD/Intel product name, the CPU model from /proc/cpuinfo, the
mainboard from DMI, or a pci.ids lookup as the general fallback.

All of this is static for the lifetime of the process, so results are cached.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

from .pciids import DEFAULT as PCI_DB
from .pciids import PciIds

DEFAULT_DMI_ROOT = "/sys/devices/virtual/dmi/id"
DEFAULT_CPUINFO = "/proc/cpuinfo"

#: Chips that measure the CPU package.
CPU_CHIPS = {"k10temp", "k8temp", "zenpower", "coretemp", "cpu_thermal", "cpu-thermal"}
#: Super-I/O and embedded controllers: these report mainboard sensors.
BOARD_CHIP_RE = re.compile(r"^(it\d|nct\d|f7\d|w83|sch\d|smsc|ite|asus|sio)", re.IGNORECASE)
#: Chips whose `device` link points at a storage device.
DISK_CHIPS = {"nvme", "drivetemp"}
#: Chips whose `device` link points at a graphics card.
GPU_CHIPS = {"amdgpu", "radeon", "nouveau", "i915", "xe", "intel_gpu"}

#: Marketing fluff that only makes the tree wider.
_NOISE_RE = re.compile(
    r"\b(corporation|corp\.?|incorporated|inc\.?|co\.?,? ?ltd\.?|ltd\.?|technology|"
    r"technologies|computer|electronics|semiconductor|systems|company)\b[,.]?",
    re.IGNORECASE,
)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            value = fh.read().strip()
    except (OSError, ValueError):
        return None
    return value or None


def _tidy(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip(" ,.-")
    return value or None


def _strip_vendor_noise(value: str) -> str:
    cleaned = _NOISE_RE.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned or value


class DeviceNamer:
    """Resolves friendly names; one instance per collector."""

    def __init__(
        self,
        dmi_root: str = DEFAULT_DMI_ROOT,
        cpuinfo: str = DEFAULT_CPUINFO,
        pci_db: PciIds = PCI_DB,
    ) -> None:
        self._dmi_root = dmi_root
        self._cpuinfo = cpuinfo
        self._pci = pci_db
        self._cache: Dict[str, Optional[str]] = {}

    # -- individual sources ------------------------------------------------
    def cpu_model(self) -> Optional[str]:
        if "cpu" in self._cache:
            return self._cache["cpu"]
        name = None
        try:
            with open(self._cpuinfo, "r", errors="replace") as fh:
                for line in fh:
                    if line.startswith(("model name", "Model name", "Hardware", "Model")):
                        _, _, value = line.partition(":")
                        name = _tidy(value)
                        if name:
                            break
        except OSError:
            name = None
        if name:
            # "AMD Ryzen 7 7800X3D 8-Core Processor" -> "AMD Ryzen 7 7800X3D"
            name = re.sub(r"\s+\d+-Core Processor.*$", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+(CPU|Processor)\b.*$", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s+@.*$", "", name)
            name = _tidy(name)
        self._cache["cpu"] = name
        return name

    def board_name(self) -> Optional[str]:
        if "board" in self._cache:
            return self._cache["board"]
        vendor = _tidy(_read(os.path.join(self._dmi_root, "board_vendor")))
        board = _tidy(_read(os.path.join(self._dmi_root, "board_name")))
        name = None
        if board:
            vendor = _strip_vendor_noise(vendor) if vendor else None
            name = f"{vendor} {board}" if vendor and not board.startswith(vendor) else board
        elif vendor:
            name = vendor
        self._cache["board"] = name
        return name

    def pci_name(self, device_dir: str) -> Optional[str]:
        """Resolve a /sys/.../0000:0c:00.0 directory through pci.ids."""
        if not device_dir:
            return None
        key = f"pci:{device_dir}"
        if key in self._cache:
            return self._cache[key]

        def hex4(filename: str) -> Optional[str]:
            raw = _read(os.path.join(device_dir, filename))
            if not raw:
                return None
            raw = raw.strip().lower()
            if raw.startswith("0x"):
                raw = raw[2:]
            return raw.zfill(4) if re.fullmatch(r"[0-9a-f]{1,4}", raw) else None

        vendor, device = hex4("vendor"), hex4("device")
        name = None
        if vendor and device:
            name = self._pci.lookup(
                vendor, device, hex4("subsystem_vendor"), hex4("subsystem_device")
            )
            name = _tidy(name)
            if name:
                name = _strip_vendor_noise(name)
        self._cache[key] = name
        return name

    def storage_model(self, device_dir: str) -> Optional[str]:
        """Model string of an NVMe controller or an ATA/SCSI disk."""
        if not device_dir:
            return None
        key = f"disk:{device_dir}"
        if key in self._cache:
            return self._cache[key]

        model = _tidy(_read(os.path.join(device_dir, "model")))
        if model:
            vendor = _tidy(_read(os.path.join(device_dir, "vendor")))
            # SCSI reports "ATA" as the vendor of every SATA disk; not useful.
            if vendor and vendor.upper() not in {"ATA", "NVME", "SCSI"}:
                if not model.upper().startswith(vendor.upper()):
                    model = f"{vendor} {model}"
        self._cache[key] = model
        return model

    def gpu_name(self, device_dir: str) -> Optional[str]:
        """AMD exposes a product name directly; everyone else goes through pci.ids."""
        if not device_dir:
            return None
        product = _tidy(_read(os.path.join(device_dir, "product_name")))
        if product and product.lower() not in {"unknown", "generic"}:
            return product
        return self.pci_name(device_dir)

    # -- the rule the backends call ---------------------------------------
    def chip_display_name(self, chip: str, device_dir: Optional[str]) -> Optional[str]:
        """Best display name for an hwmon chip, or None to keep the chip name."""
        lowered = chip.lower()

        if lowered in CPU_CHIPS:
            return self.cpu_model()
        if lowered in DISK_CHIPS and device_dir:
            return self.storage_model(device_dir)
        if lowered in GPU_CHIPS and device_dir:
            return self.gpu_name(device_dir)
        if BOARD_CHIP_RE.match(lowered):
            return self.board_name()
        if device_dir:
            # Anything else that sits on the PCI bus, e.g. a Wi-Fi card.
            return self.pci_name(device_dir)
        return None
