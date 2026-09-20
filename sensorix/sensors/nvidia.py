"""NVIDIA GPU backend driven by `nvidia-smi`.

NVIDIA's proprietary driver does not publish temperatures through hwmon, so the
only portable way to read them is the query interface of nvidia-smi.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Tuple

from .model import Group, Reading, sort_readings

_FIELDS = [
    ("index", None, None),
    ("name", None, None),
    ("temperature.gpu", "temp", "GPU Core"),
    ("fan.speed", "pwm", "GPU Fan"),
    ("utilization.gpu", "load", "GPU Load"),
    ("utilization.memory", "load", "Memory Controller Load"),
    ("power.draw", "power", "GPU Power"),
    ("clocks.sm", "freq", "GPU Core Clock"),
    ("clocks.mem", "freq", "GPU Memory Clock"),
    ("memory.used", "memory", "Memory Used"),
    ("memory.total", "memory", "Memory Total"),
]

_QUERY = ",".join(field for field, _, _ in _FIELDS)


def available() -> bool:
    return shutil.which("nvidia-smi") is not None


def _to_float(raw: str) -> Optional[float]:
    raw = raw.strip()
    if not raw or raw.startswith("[") or raw.lower() in {"n/a", "unknown"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def read_nvidia(timeout: float = 5.0) -> Tuple[List[Group], List[str]]:
    if not available():
        return [], []
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], [f"nvidia-smi の実行に失敗しました: {exc}"]

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return [], [f"nvidia-smi エラー: {detail[0] if detail else proc.returncode}"]

    groups: List[Group] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) != len(_FIELDS):
            continue
        index, name = cells[0], cells[1]
        group_key = f"nvidia:{index}"
        readings: List[Reading] = []
        for cell, (field, kind, label) in zip(cells, _FIELDS):
            if kind is None:
                continue
            value = _to_float(cell)
            if value is None:
                continue
            readings.append(
                Reading(
                    key=f"{group_key}/{field}",
                    label=label,
                    kind=kind,
                    value=value,
                    limit=None,
                )
            )
        if readings:
            groups.append(
                Group(
                    key=group_key,
                    name=name or f"NVIDIA GPU {index}",
                    detail=f"nvidia-smi · GPU {index}",
                    readings=sort_readings(readings),
                )
            )
    return groups, []
