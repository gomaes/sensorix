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
from typing import Dict, Optional, Tuple

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
#: DDR5 modules carry an SPD hub with a temperature sensor, one per slot.
DIMM_CHIPS = {"spd5118", "jc42", "ee1004"}

#: Last-resort descriptions, so a chip is never just a bare driver name.
#: Looked up after any trailing "_1" instance suffix has been removed.
GENERIC_CHIP_NAMES = {
    "acpitz": "ACPI Thermal Zone",
    "thermal": "Thermal Zone",
    "pch": "Intel PCH",
    "pch_cannonlake": "Intel PCH",
    "pch_skylake": "Intel PCH",
    "iwlwifi": "Wi-Fi Adapter",
    "mt7921": "Wi-Fi Adapter",
    "mt7922": "Wi-Fi Adapter",
    "ath10k": "Wi-Fi Adapter",
    "ath11k": "Wi-Fi Adapter",
    "ath12k": "Wi-Fi Adapter",
    "bat": "Battery",
    "bat0": "Battery",
    "bat1": "Battery",
    "ucsi_source_psy": "USB-C Power Supply",
    "nzxt_smart2": "AIO Cooler",
    "kraken": "AIO Cooler",
    "kraken3": "AIO Cooler",
    "corsaircpro": "Fan Controller",
    "aquacomputer": "Fan Controller",
    "nvme": "NVMe SSD",
    "drivetemp": "Drive",
    "amdgpu": "AMD GPU",
    "radeon": "AMD GPU",
    "nouveau": "NVIDIA GPU",
    "i915": "Intel GPU",
    "xe": "Intel GPU",
}

#: hwmon appends an instance number when a driver registers more than one chip.
_INSTANCE_SUFFIX_RE = re.compile(r"_\d+$")
#: A PCI slot directory: 0000:0c:00.0
_PCI_ADDRESS_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d$", re.IGNORECASE)
#: An I2C client directory: 0-0050 (bus 0, address 0x50)
_I2C_ADDRESS_RE = re.compile(r"^(\d+)-([0-9a-f]{4})$", re.IGNORECASE)
#: DDR5 SPD: 30 ASCII bytes of module part number at offset 0x209.
_SPD_PART_OFFSET = 0x209
_SPD_PART_LENGTH = 30

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
        self._cache_pairs: Dict[str, Optional[Tuple[str, Optional[str]]]] = {}

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
            name = re.sub(r"\((?:R|TM|r|tm)\)", "", name)
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
        """Resolve a device through pci.ids, walking up to its PCI parent.

        Several drivers register their hwmon node against an intermediate
        device rather than the card itself - iwlwifi hangs it off the wiphy,
        for instance - so the IDs live one or more levels up the tree.
        """
        if not device_dir:
            return None
        key = f"pci:{device_dir}"
        if key in self._cache:
            return self._cache[key]

        pci_dir = _nearest_pci_device(device_dir)
        if pci_dir is None:
            self._cache[key] = None
            return None
        device_dir = pci_dir

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

    def dimm_name(self, device_dir: str) -> Optional[Tuple[str, Optional[str]]]:
        """Name a DDR5 module from the I2C address of its SPD hub.

        The SPD hubs sit at 0x50..0x57, one per slot, which is the only
        unprivileged way to tell four identically named `spd5118` chips apart.
        The module part number is read too when the SPD is readable - it
        usually is not, since the EEPROM is root-only on most systems.
        """
        if not device_dir:
            return None
        key = f"dimm:{device_dir}"
        if key in self._cache_pairs:
            return self._cache_pairs[key]

        result: Optional[Tuple[str, Optional[str]]] = None
        match = _I2C_ADDRESS_RE.match(os.path.basename(device_dir))
        if match is not None:
            address = int(match.group(2), 16)
            if 0x50 <= address <= 0x57:
                slot = address - 0x50 + 1
                result = (f"DDR5 DIMM {slot}", _spd_part_number(device_dir))
        self._cache_pairs[key] = result
        return result

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
        resolved = self.describe_chip(chip, device_dir)
        return resolved[0] if resolved else None

    def describe_chip(
        self, chip: str, device_dir: Optional[str]
    ) -> Optional[Tuple[str, Optional[str]]]:
        """Return (display name, extra detail) for an hwmon chip.

        Resolution runs from most to least specific: the exact model of the
        part, then the machine part it belongs to, then a PCI lookup, and
        finally a plain-language description so that nothing is ever shown as
        an unexplained driver name.
        """
        lowered = chip.lower()
        base = _INSTANCE_SUFFIX_RE.sub("", lowered)

        if lowered in CPU_CHIPS or base in CPU_CHIPS:
            named = self.cpu_model()
            if named:
                return named, None
        elif (lowered in DISK_CHIPS or base in DISK_CHIPS) and device_dir:
            named = self.storage_model(device_dir)
            if named:
                return named, None
        elif (lowered in GPU_CHIPS or base in GPU_CHIPS) and device_dir:
            named = self.gpu_name(device_dir)
            if named:
                return named, None
        elif (lowered in DIMM_CHIPS or base in DIMM_CHIPS) and device_dir:
            dimm = self.dimm_name(device_dir)
            if dimm:
                return dimm
        elif BOARD_CHIP_RE.match(lowered):
            named = self.board_name()
            if named:
                return named, None

        if device_dir:
            named = self.pci_name(device_dir)
            if named:
                return named, None

        generic = GENERIC_CHIP_NAMES.get(lowered) or GENERIC_CHIP_NAMES.get(base)
        if generic:
            return generic, None
        return None


def _nearest_pci_device(start: str) -> Optional[str]:
    """Walk up the sysfs tree to the PCI function this device belongs to."""
    path = os.path.realpath(start)
    for _ in range(12):  # /sys/devices/... is never anywhere near this deep
        if not path or path == "/" or os.path.basename(path) == "devices":
            return None
        if _PCI_ADDRESS_RE.match(os.path.basename(path)) and os.path.exists(
            os.path.join(path, "vendor")
        ):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def _spd_part_number(device_dir: str) -> Optional[str]:
    """Module part number out of a DDR5 SPD image, when it can be read."""
    for candidate in _spd_candidates(device_dir):
        try:
            with open(candidate, "rb") as fh:
                fh.seek(_SPD_PART_OFFSET)
                raw = fh.read(_SPD_PART_LENGTH)
        except (OSError, ValueError):
            continue  # almost always EACCES: the SPD EEPROM is root-only
        if not raw:
            continue
        text = raw.decode("ascii", errors="ignore")
        text = "".join(ch for ch in text if 0x20 <= ord(ch) < 0x7F).strip()
        if len(text) >= 4:
            return text
    return None


def _spd_candidates(device_dir: str):
    """Places the SPD image shows up, across driver and kernel versions."""
    yield os.path.join(device_dir, "eeprom")
    yield os.path.join(device_dir, "nvmem")
    try:
        for entry in sorted(os.listdir(device_dir)):
            if entry.startswith("nvmem"):
                yield os.path.join(device_dir, entry, "nvmem")
    except OSError:
        return
