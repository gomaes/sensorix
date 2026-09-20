"""Fallback backend built on top of `sensors -j` (lm_sensors).

The sysfs backend is preferred because it is dependency free, but `sensors`
applies the per-board configuration from /etc/sensors.d, so it sometimes has
nicer labels and correct voltage dividers.  It is also a useful escape hatch
when a chip is exposed through a path we do not walk.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, List, Optional, Tuple

from .model import Group, Reading, sort_readings

#: `sensors -j` reports values in their final unit, so no scaling is needed.
_INPUT_RE = re.compile(r"^(temp|fan|in|curr|power|energy|humidity|freq)(\d+)_input$")
_PWM_RE = re.compile(r"^pwm(\d+)$")
_BUS_RE = re.compile(
    r"^(?P<chip>.+)-(?:pci|isa|i2c|spi|virtual|acpi|platform|hid|mdio|scsi|usb)-[0-9a-fA-F]+$"
)

_KIND_BY_PREFIX = {
    "temp": "temp",
    "fan": "fan",
    "in": "volt",
    "curr": "curr",
    "power": "power",
    "energy": "energy",
    "humidity": "humidity",
    "freq": "freq",
}


def available() -> bool:
    return shutil.which("sensors") is not None


def _loads_tolerant(text: str) -> Any:
    """`sensors -j` in some releases emits trailing commas; repair those."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = re.sub(r",(\s*[}\]])", r"\1", text)
        return json.loads(repaired)


def run_sensors(timeout: float = 5.0) -> Tuple[Optional[Any], Optional[str]]:
    if not available():
        return None, "sensors コマンドが見つかりません (pacman -S lm_sensors)"
    try:
        proc = subprocess.run(
            ["sensors", "-j"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"sensors の実行に失敗しました: {exc}"
    if not proc.stdout.strip():
        detail = proc.stderr.strip() or f"exit={proc.returncode}"
        return None, f"sensors が何も出力しませんでした ({detail})"
    try:
        return _loads_tolerant(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"sensors -j の出力を解析できません: {exc}"


def _chip_label(raw: str) -> Tuple[str, str]:
    match = _BUS_RE.match(raw)
    if match:
        return match.group("chip"), raw
    return raw, raw


def parse(payload: Any) -> List[Group]:
    """Turn a decoded `sensors -j` document into groups."""
    groups: List[Group] = []
    if not isinstance(payload, dict):
        return groups

    for chip_raw, chip_body in payload.items():
        if not isinstance(chip_body, dict):
            continue
        name, full = _chip_label(str(chip_raw))
        group_key = f"sensors:{full}"
        adapter = chip_body.get("Adapter")
        readings: List[Reading] = []

        for label, body in chip_body.items():
            if label == "Adapter" or not isinstance(body, dict):
                continue
            for field_name, value in body.items():
                if not isinstance(value, (int, float)):
                    continue
                match = _INPUT_RE.match(field_name)
                if match:
                    prefix, index = match.group(1), match.group(2)
                    limit = None
                    if prefix == "temp":
                        for suffix in ("crit", "emergency", "max"):
                            candidate = body.get(f"temp{index}_{suffix}")
                            if isinstance(candidate, (int, float)) and candidate > 0:
                                limit = float(candidate)
                                break
                    readings.append(
                        Reading(
                            key=f"{group_key}/{field_name}",
                            label=str(label),
                            kind=_KIND_BY_PREFIX[prefix],
                            value=float(value),
                            limit=limit,
                        )
                    )
                    continue
                if _PWM_RE.match(field_name):
                    readings.append(
                        Reading(
                            key=f"{group_key}/{field_name}",
                            label=str(label),
                            kind="pwm",
                            value=float(value) / 255.0 * 100.0,
                        )
                    )

        if readings:
            groups.append(
                Group(
                    key=group_key,
                    name=name,
                    detail=str(adapter) if adapter else full,
                    readings=sort_readings(readings),
                )
            )
    return groups


def read_lmsensors(timeout: float = 5.0) -> Tuple[List[Group], List[str]]:
    payload, error = run_sensors(timeout=timeout)
    if error:
        return [], [error]
    return parse(payload), []
