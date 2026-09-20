"""Sensor polling, moved off the GUI thread.

Reading sysfs is cheap, but `sensors -j` and `nvidia-smi` are subprocesses that
can block for tens of milliseconds - long enough to make the window stutter.
The worker lives in its own QThread and only ever reacts to queued signals; the
QTimer that drives it stays on the GUI thread, so there is no timer to tear
down across thread boundaries at shutdown.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from ..sensors.collector import Collector
from ..sensors.model import Sample


class SamplerWorker(QObject):
    sampled = Signal(object)  # Sample

    def __init__(self, collector: Collector) -> None:
        super().__init__()
        self._collector = collector

    @Slot()
    def poll(self) -> None:
        self.sampled.emit(self._sample())

    @Slot(str)
    def set_source(self, source: str) -> None:
        self._collector.config.source = source
        self._collector.reset_stats()
        self.sampled.emit(self._sample())

    @Slot()
    def reset_stats(self) -> None:
        self._collector.reset_stats()
        self.sampled.emit(self._sample())

    def _sample(self) -> Sample:
        try:
            return self._collector.sample()
        except Exception as exc:  # noqa: BLE001 - polling must never kill the thread
            return Sample(groups=[], errors=[f"収集に失敗しました: {exc}"], sources=[])
