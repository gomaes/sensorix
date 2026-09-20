"""Sensor backends: sysfs hwmon, lm_sensors and nvidia-smi."""

from .collector import (
    SOURCE_AUTO,
    SOURCE_HWMON,
    SOURCE_SENSORS,
    Collector,
    CollectorConfig,
)
from .model import Group, Kind, KINDS, Reading, Sample, Stats, StatsStore, kind_of

__all__ = [
    "Collector",
    "CollectorConfig",
    "SOURCE_AUTO",
    "SOURCE_HWMON",
    "SOURCE_SENSORS",
    "Group",
    "Reading",
    "Sample",
    "Stats",
    "StatsStore",
    "Kind",
    "KINDS",
    "kind_of",
]
