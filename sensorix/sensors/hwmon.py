"""Reads sensors straight out of /sys/class/hwmon/hwmon*/.

This is the primary backend: it needs no external tool, no root privileges and
no daemon.  Everything the kernel exposes for a chip lives in one flat
directory, e.g. for `k10temp`::

    /sys/class/hwmon/hwmon3/name          -> "k10temp"
    /sys/class/hwmon/hwmon3/temp1_label   -> "Tctl"
    /sys/class/hwmon/hwmon3/temp1_input   -> "45125"   (millidegrees)
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from . import devinfo
from .devinfo import DeviceNamer
from .model import Group, Reading, sort_readings

DEFAULT_HWMON_ROOT = "/sys/class/hwmon"
DEFAULT_DRM_ROOT = "/sys/class/drm"

#: sysfs prefix -> (kind, scale applied to the raw integer, default label)
SENSOR_TYPES: Dict[str, Tuple[str, float, str]] = {
    "temp": ("temp", 1e-3, "Temp"),
    "fan": ("fan", 1.0, "Fan"),
    "in": ("volt", 1e-3, "Voltage"),
    "curr": ("curr", 1e-3, "Current"),
    "power": ("power", 1e-6, "Power"),
    "energy": ("energy", 1e-6, "Energy"),
    "freq": ("freq", 1e-6, "Clock"),
    "humidity": ("humidity", 1e-3, "Humidity"),
}

_INPUT_RE = re.compile(r"^(%s)(\d+)_input$" % "|".join(SENSOR_TYPES))
_PWM_RE = re.compile(r"^pwm(\d+)$")
_GENERIC_TEMP_RE = re.compile(r"^Temp \d+$")

#: Chip names that are noisy and carry no real sensor value.
_BORING_CHIPS = {"acpitz_dummy"}


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return None


def _read_number(path: str) -> Optional[float]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _gpu_cards(drm_root: str) -> Dict[str, str]:
    """Map the real path of a hwmon directory to its DRM card name.

    AMD and Intel GPUs publish their sensors below
    `/sys/class/drm/card0/device/hwmon/hwmon5`, and the very same directory is
    also linked from /sys/class/hwmon.  Resolving both sides lets us label the
    group "amdgpu (card0)" instead of just "amdgpu".
    """
    mapping: Dict[str, str] = {}
    try:
        cards = sorted(os.listdir(drm_root))
    except OSError:
        return mapping
    for card in cards:
        if not re.fullmatch(r"card\d+", card):
            continue
        hwmon_dir = os.path.join(drm_root, card, "device", "hwmon")
        try:
            entries = os.listdir(hwmon_dir)
        except OSError:
            continue
        for entry in entries:
            real = os.path.realpath(os.path.join(hwmon_dir, entry))
            mapping[real] = card
    return mapping


def _device_detail(hwmon_dir: str) -> Optional[str]:
    """Where this chip lives, e.g. "0000:0c:00.0" or "nvme0" - None if unknown."""
    device = os.path.join(hwmon_dir, "device")
    if os.path.exists(device):
        base = os.path.basename(os.path.realpath(device))
        if base and base != "device" and base != os.path.basename(hwmon_dir):
            return base
    return None


def _label_for(hwmon_dir: str, prefix: str, index: str) -> str:
    label = _read_text(os.path.join(hwmon_dir, f"{prefix}{index}_label"))
    if label:
        return label
    return f"{SENSOR_TYPES[prefix][2]} {index}"


def _temp_limit(hwmon_dir: str, index: str) -> Optional[float]:
    """Preferred alarm point for a temperature channel, in °C."""
    for suffix in ("crit", "emergency", "max"):
        raw = _read_number(os.path.join(hwmon_dir, f"temp{index}_{suffix}"))
        if raw is not None and raw > 0:
            return raw / 1000.0
    return None


def _gpu_extras(drm_root: str, card: str, group_key: str) -> List[Reading]:
    """AMD/Intel GPU counters that live next to the hwmon directory."""
    base = os.path.join(drm_root, card, "device")
    readings: List[Reading] = []

    busy = _read_number(os.path.join(base, "gpu_busy_percent"))
    if busy is not None:
        readings.append(
            Reading(key=f"{group_key}/gpu_busy", label="GPU Load", kind="load", value=busy)
        )
    mem_busy = _read_number(os.path.join(base, "mem_busy_percent"))
    if mem_busy is not None:
        readings.append(
            Reading(key=f"{group_key}/mem_busy", label="Memory Load", kind="load", value=mem_busy)
        )
    used = _read_number(os.path.join(base, "mem_info_vram_used"))
    total = _read_number(os.path.join(base, "mem_info_vram_total"))
    if used is not None:
        readings.append(
            Reading(
                key=f"{group_key}/vram_used",
                label="Memory Used",
                kind="memory",
                value=used / (1024.0 * 1024.0),
            )
        )
        if total and total > 0:
            readings.append(
                Reading(
                    key=f"{group_key}/vram_pct",
                    label="Memory Usage",
                    kind="load",
                    value=used / total * 100.0,
                )
            )
    return readings


def read_hwmon(
    hwmon_root: str = DEFAULT_HWMON_ROOT,
    drm_root: str = DEFAULT_DRM_ROOT,
    namer: Optional[DeviceNamer] = None,
) -> Tuple[List[Group], List[str]]:
    """Scan every hwmon device.  Returns (groups, non-fatal error messages)."""
    namer = namer or DeviceNamer()
    groups: List[Group] = []
    errors: List[str] = []

    if not os.path.isdir(hwmon_root):
        return groups, [f"{hwmon_root} が見つかりません (hwmon 非対応カーネル?)"]

    try:
        entries = sorted(
            os.listdir(hwmon_root), key=lambda n: int(n[5:]) if n[5:].isdigit() else 1 << 30
        )
    except OSError as exc:
        return groups, [f"{hwmon_root} を読めません: {exc}"]

    cards = _gpu_cards(drm_root)

    for entry in entries:
        if not entry.startswith("hwmon"):
            continue
        hwmon_dir = os.path.join(hwmon_root, entry)
        try:
            group = _read_device(hwmon_dir, entry, cards, drm_root, namer)
        except OSError as exc:
            errors.append(f"{entry}: {exc}")
            continue
        if group is not None:
            groups.append(group)

    if not groups and not errors:
        errors.append(f"{hwmon_root} にセンサーが見つかりませんでした")
    return groups, errors


def _read_device(
    hwmon_dir: str,
    entry: str,
    cards: Dict[str, str],
    drm_root: str,
    namer: DeviceNamer,
) -> Optional[Group]:
    name = _read_text(os.path.join(hwmon_dir, "name")) or entry
    if name in _BORING_CHIPS:
        return None

    try:
        files = os.listdir(hwmon_dir)
    except OSError:
        # Some virtual devices vanish between the listdir above and this call.
        return None

    group_key = f"hwmon:{entry}"
    readings: List[Reading] = []

    for filename in files:
        match = _INPUT_RE.match(filename)
        if match:
            prefix, index = match.group(1), match.group(2)
            raw = _read_number(os.path.join(hwmon_dir, filename))
            if raw is None:
                continue
            kind, scale, _ = SENSOR_TYPES[prefix]
            limit = _temp_limit(hwmon_dir, index) if prefix == "temp" else None
            readings.append(
                Reading(
                    key=f"{group_key}/{prefix}{index}",
                    label=_label_for(hwmon_dir, prefix, index),
                    kind=kind,
                    value=raw * scale,
                    limit=limit,
                )
            )
            continue

        pwm = _PWM_RE.match(filename)
        if pwm:
            raw = _read_number(os.path.join(hwmon_dir, filename))
            if raw is None:
                continue
            index = pwm.group(1)
            label = _read_text(os.path.join(hwmon_dir, f"pwm{index}_label")) or f"PWM {index}"
            readings.append(
                Reading(
                    key=f"{group_key}/pwm{index}",
                    label=label,
                    kind="pwm",
                    value=raw / 255.0 * 100.0,
                )
            )

    parts = [entry]
    card = cards.get(os.path.realpath(hwmon_dir))
    if card:
        readings.extend(_gpu_extras(drm_root, card, group_key))
        parts.append(card)
    location = _device_detail(hwmon_dir)
    if location:
        parts.append(location)

    if not readings:
        return None

    device_link = os.path.join(hwmon_dir, "device")
    device_dir = os.path.realpath(device_link) if os.path.exists(device_link) else None

    described = namer.describe_chip(name, device_dir)
    display, extra = described if described is not None else (None, None)

    # A device with a single unlabelled channel reads better as "Temperature"
    # than as "Temp 1"; most of these are DIMMs, thermal zones and Wi-Fi cards.
    temps = [r for r in readings if r.kind == "temp"]
    if len(temps) == 1 and _GENERIC_TEMP_RE.match(temps[0].label):
        readings = [
            replace(r, label="Temperature") if r is temps[0] else r for r in readings
        ]
    # Only fold an hwmon chip into another group when it really describes one
    # physical device; a Super-I/O chip shares its parent with unrelated nodes.
    merge_key = (
        f"dev:{device_dir}" if device_dir and name.lower() in devinfo.DISK_CHIPS else None
    )

    detail = [name] if display else []
    if extra:
        detail.append(extra)
    detail.extend(parts)

    return Group(
        key=group_key,
        name=display or name,
        detail=" · ".join(detail),
        chip=name,
        readings=sort_readings(readings),
        merge_key=merge_key,
    )
