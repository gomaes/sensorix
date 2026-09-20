"""The main window: a HWMonitor-style sensor tree."""

from __future__ import annotations

import datetime as _dt
import os
from typing import Dict, List, Optional

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from .. import APP_NAME, __version__
from ..sensors.collector import (
    SOURCE_AUTO,
    SOURCE_HWMON,
    SOURCE_SENSORS,
    Collector,
)
from ..sensors.model import Group, Reading, Sample
from . import theme
from .worker import SamplerWorker

COL_NAME, COL_VALUE, COL_MIN, COL_MAX, COL_AVG = range(5)
HEADERS = ["Name", "Value", "Min", "Max", "Avg"]

_ALARM_BRUSH = QBrush(QColor(theme.ALARM))
_VALUE_BRUSH = QBrush(QColor(theme.VALUE_TEXT))
_STAT_BRUSH = QBrush(QColor(theme.TEXT_DIM))
_NAME_BRUSH = QBrush(QColor(theme.TEXT))
_GROUP_BRUSH = QBrush(QColor(theme.GROUP_TEXT))
_SECTION_BRUSH = QBrush(QColor(theme.ACCENT))

_ROLE_ALARM = Qt.ItemDataRole.UserRole + 1

INTERVALS = [("0.5 秒", 500), ("1 秒", 1000), ("2 秒", 2000), ("5 秒", 5000)]
SOURCES = [
    ("自動 (hwmon → lm_sensors)", SOURCE_AUTO),
    ("sysfs hwmon のみ", SOURCE_HWMON),
    ("lm_sensors (sensors -j) のみ", SOURCE_SENSORS),
]


class MainWindow(QMainWindow):
    pollRequested = Signal()
    sourceChanged = Signal(str)
    resetRequested = Signal()

    def __init__(
        self,
        collector: Collector,
        interval_ms: int = 1000,
        temp_threshold: float = 80.0,
        always_on_top: bool = False,
    ) -> None:
        super().__init__()
        self._collector = collector
        self._interval_ms = interval_ms
        self._threshold = temp_threshold
        self._settings = QSettings("sensorix", "Sensorix")
        self._last_sample: Optional[Sample] = None
        self._busy = False
        self._group_items: Dict[str, QTreeWidgetItem] = {}
        self._section_items: Dict[str, QTreeWidgetItem] = {}
        self._reading_items: Dict[str, QTreeWidgetItem] = {}
        self._warned_about_empty = False
        self._show_chips = self._settings.value("view/showChipNames", False, type=bool)

        self.setWindowTitle(APP_NAME)
        self._mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._mono.setPointSize(max(8, self._mono.pointSize() - 1))
        self._group_font = QFont()
        self._group_font.setBold(True)
        self._section_font = QFont()
        self._section_font.setBold(True)

        self._build_tree()
        self._build_menu()
        self._build_status_bar()

        if always_on_top:
            self._on_top_action.setChecked(True)
            self._apply_always_on_top(True)

        self._restore_geometry()
        self._start_worker()

    # -- construction -----------------------------------------------------
    def _build_tree(self) -> None:
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSortingEnabled(False)
        self.tree.setExpandsOnDoubleClick(True)

        header = self.tree.header()
        # The name column soaks up the spare width so the four value columns
        # always stay visible - no horizontal scrollbar, like HWMonitor.
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (COL_VALUE, COL_MIN, COL_MAX, COL_AVG):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(column, 90)
        header.setMinimumSectionSize(64)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in (COL_VALUE, COL_MIN, COL_MAX, COL_AVG):
            self.tree.headerItem().setTextAlignment(
                column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

        self.setCentralWidget(self.tree)

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("ファイル(&F)")
        save = QAction("監視データを保存(&S)...", self)
        save.setShortcut(QKeySequence.StandardKey.Save)
        save.triggered.connect(self._save_report)
        file_menu.addAction(save)
        file_menu.addSeparator()
        quit_action = QAction("終了(&X)", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("表示(&V)")
        self._on_top_action = QAction("常に最前面に表示(&T)", self)
        self._on_top_action.setCheckable(True)
        self._on_top_action.setShortcut(QKeySequence("Ctrl+T"))
        self._on_top_action.toggled.connect(self._apply_always_on_top)
        view_menu.addAction(self._on_top_action)
        view_menu.addSeparator()

        self._chip_names_action = QAction("モデル名ではなくチップ名を表示(&C)", self)
        self._chip_names_action.setCheckable(True)
        self._chip_names_action.setChecked(self._show_chips)
        self._chip_names_action.setToolTip(
            "Samsung SSD 980 PRO ではなく nvme のように、ドライバ名で表示します"
        )
        self._chip_names_action.toggled.connect(self._set_show_chips)
        view_menu.addAction(self._chip_names_action)
        view_menu.addSeparator()

        expand = QAction("すべて展開(&E)", self)
        expand.triggered.connect(self.tree.expandAll)
        view_menu.addAction(expand)
        collapse = QAction("すべて折りたたむ(&C)", self)
        collapse.triggered.connect(self.tree.collapseAll)
        view_menu.addAction(collapse)

        monitor_menu = menubar.addMenu("計測(&M)")
        reset = QAction("Min/Max/Avg をリセット(&R)", self)
        reset.setShortcut(QKeySequence("Ctrl+R"))
        reset.triggered.connect(self.resetRequested.emit)
        monitor_menu.addAction(reset)
        monitor_menu.addSeparator()

        interval_menu = monitor_menu.addMenu("更新間隔(&I)")
        interval_group = QActionGroup(self)
        interval_group.setExclusive(True)
        for label, value in INTERVALS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(value == self._interval_ms)
            action.triggered.connect(lambda _checked, ms=value: self._set_interval(ms))
            interval_group.addAction(action)
            interval_menu.addAction(action)

        source_menu = monitor_menu.addMenu("データ取得元(&S)")
        source_group = QActionGroup(self)
        source_group.setExclusive(True)
        for label, value in SOURCES:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(value == self._collector.config.source)
            action.triggered.connect(lambda _checked, src=value: self._set_source(src))
            source_group.addAction(action)
            source_menu.addAction(action)

        threshold = QAction("温度の警告しきい値(&W)...", self)
        threshold.triggered.connect(self._edit_threshold)
        monitor_menu.addAction(threshold)

        help_menu = menubar.addMenu("ヘルプ(&H)")
        diagnostics = QAction("診断情報(&D)...", self)
        diagnostics.triggered.connect(self._show_diagnostics)
        help_menu.addAction(diagnostics)
        about = QAction("バージョン情報(&A)...", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _build_status_bar(self) -> None:
        self._status_sensors = QLabel("センサーを検索中...")
        self._status_source = QLabel("")
        self._status_time = QLabel("")
        self._status_warning = QLabel("")
        self._status_warning.setStyleSheet(f"color: {theme.ALARM};")
        bar = self.statusBar()
        bar.addWidget(self._status_sensors)
        bar.addWidget(self._status_source)
        bar.addWidget(self._status_warning, 1)
        bar.addPermanentWidget(self._status_time)

    def _start_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = SamplerWorker(self._collector)
        self._worker.moveToThread(self._thread)
        self._worker.sampled.connect(self._on_sample)
        self.pollRequested.connect(self._worker.poll)
        self.sourceChanged.connect(self._worker.set_source)
        self.resetRequested.connect(self._worker.reset_stats)
        self._thread.start()

        # The timer stays on the GUI thread and only asks for a new sample once
        # the previous one came back, so a slow backend drops a beat instead of
        # piling requests up in the worker's queue.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._request_sample)
        self._timer.start()
        self._request_sample()

    def _request_sample(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.pollRequested.emit()

    # -- sample handling --------------------------------------------------
    def _on_sample(self, sample: Sample) -> None:
        self._busy = False
        self._last_sample = sample
        self._render(sample)

    def _render(self, sample: Sample) -> None:
        self.tree.setUpdatesEnabled(False)
        try:
            self._sync_tree(sample.groups)
        finally:
            self.tree.setUpdatesEnabled(True)
        self._update_status(sample)

    def _sync_tree(self, groups: List[Group]) -> None:
        seen_groups = set()
        seen_sections = set()
        seen_readings = set()

        for position, group in enumerate(groups):
            seen_groups.add(group.key)
            item = self._group_items.get(group.key)
            if item is None:
                item = QTreeWidgetItem([group.name, "", "", "", ""])
                item.setFont(COL_NAME, self._group_font)
                item.setForeground(COL_NAME, _GROUP_BRUSH)
                self.tree.insertTopLevelItem(min(position, self.tree.topLevelItemCount()), item)
                item.setExpanded(True)
                self._group_items[group.key] = item
            item.setText(COL_NAME, self._group_label(group))
            item.setToolTip(COL_NAME, self._group_tooltip(group))

            # Readings arrive already ordered by section, so appending as we
            # go puts the section headers in their declared order.
            section_names = dict(group.sections)
            for reading in group.readings:
                seen_readings.add(reading.key)
                parent = item
                if reading.section is not None:
                    parent = self._section_item(
                        group, item, reading.section, section_names, seen_sections
                    )
                child = self._reading_items.get(reading.key)
                if child is None:
                    child = QTreeWidgetItem(parent)
                    child.setForeground(COL_NAME, _NAME_BRUSH)
                    for column in (COL_VALUE, COL_MIN, COL_MAX, COL_AVG):
                        child.setFont(column, self._mono)
                        child.setTextAlignment(
                            column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        )
                    child.setForeground(COL_VALUE, _VALUE_BRUSH)
                    child.setForeground(COL_MIN, _STAT_BRUSH)
                    child.setForeground(COL_MAX, _STAT_BRUSH)
                    child.setForeground(COL_AVG, _STAT_BRUSH)
                    child.setText(COL_NAME, reading.label)
                    child.setToolTip(COL_NAME, f"{group.name} · {reading.key}")
                    self._reading_items[reading.key] = child
                elif child.parent() is not parent:
                    # The row moved between sources, or gained a section once
                    # the CPU topology became available.
                    old_parent = child.parent()
                    if old_parent is not None:
                        old_parent.removeChild(child)
                    parent.addChild(child)
                self._update_row(child, reading)

        self._prune(seen_groups, seen_sections, seen_readings)

    def _section_item(
        self,
        group: Group,
        group_item: QTreeWidgetItem,
        key: str,
        names: Dict[str, str],
        seen: set,
    ) -> QTreeWidgetItem:
        full_key = f"{group.key}#{key}"
        seen.add(full_key)
        node = self._section_items.get(full_key)
        if node is None:
            node = QTreeWidgetItem(group_item)
            node.setFont(COL_NAME, self._section_font)
            node.setForeground(COL_NAME, _SECTION_BRUSH)
            self._section_items[full_key] = node
            node.setExpanded(True)
        node.setText(COL_NAME, names.get(key, key))
        return node

    def _group_label(self, group: Group) -> str:
        if self._show_chips and group.chip:
            return group.chip
        return group.name

    def _group_tooltip(self, group: Group) -> str:
        parts = [group.name]
        if group.chip and group.chip != group.name:
            parts.append(group.chip)
        if group.detail:
            parts.append(group.detail)
        return " · ".join(parts)

    def _set_show_chips(self, enabled: bool) -> None:
        self._show_chips = enabled
        if self._last_sample is not None:
            self._render(self._last_sample)

    def _update_row(self, item: QTreeWidgetItem, reading: Reading) -> None:
        item.setText(COL_VALUE, reading.text)
        stats = reading.stats
        if stats is not None:
            item.setText(COL_MIN, reading.format(stats.minimum))
            item.setText(COL_MAX, reading.format(stats.maximum))
            item.setText(COL_AVG, reading.format(stats.average))
        else:
            for column in (COL_MIN, COL_MAX, COL_AVG):
                item.setText(column, "-")

        alarm = reading.is_alarm(self._threshold)
        if item.data(COL_NAME, _ROLE_ALARM) == alarm:
            return
        item.setData(COL_NAME, _ROLE_ALARM, alarm)
        if alarm:
            for column in range(len(HEADERS)):
                item.setForeground(column, _ALARM_BRUSH)
        else:
            item.setForeground(COL_NAME, _NAME_BRUSH)
            item.setForeground(COL_VALUE, _VALUE_BRUSH)
            item.setForeground(COL_MIN, _STAT_BRUSH)
            item.setForeground(COL_MAX, _STAT_BRUSH)
            item.setForeground(COL_AVG, _STAT_BRUSH)

    def _prune(self, seen_groups, seen_sections, seen_readings) -> None:
        """Drop rows for hardware that disappeared (USB device unplugged, ...)."""
        for key in [k for k in self._reading_items if k not in seen_readings]:
            item = self._reading_items.pop(key)
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
        for key in [k for k in self._section_items if k not in seen_sections]:
            item = self._section_items.pop(key)
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
        for key in [k for k in self._group_items if k not in seen_groups]:
            item = self._group_items.pop(key)
            index = self.tree.indexOfTopLevelItem(item)
            if index >= 0:
                self.tree.takeTopLevelItem(index)

    def _update_status(self, sample: Sample) -> None:
        self._status_sensors.setText(
            f"{len(sample.groups)} チップ / {sample.sensor_count} センサー"
        )
        self._status_source.setText(" · ".join(sample.sources) if sample.sources else "")
        self._status_time.setText(_dt.datetime.now().strftime("最終更新 %H:%M:%S"))

        if sample.errors:
            first = sample.errors[0]
            suffix = f" (他 {len(sample.errors) - 1} 件)" if len(sample.errors) > 1 else ""
            self._status_warning.setText(f"⚠ {first}{suffix}")
            self._status_warning.setToolTip("\n".join(sample.errors))
        else:
            self._status_warning.setText("")
            self._status_warning.setToolTip("")

        if not sample.groups and not self._warned_about_empty:
            self._warned_about_empty = True
            QMessageBox.warning(
                self,
                APP_NAME,
                "センサーを 1 つも取得できませんでした。\n\n"
                "・lm_sensors を導入し `sudo sensors-detect` を実行してください\n"
                "・必要なカーネルモジュール (it87, nct6775 など) が読み込まれているか確認してください\n"
                "・仮想マシンやコンテナ内では hwmon が公開されない場合があります\n\n"
                + ("\n".join(sample.errors) if sample.errors else ""),
            )

    # -- menu actions -----------------------------------------------------
    def _set_interval(self, interval_ms: int) -> None:
        self._interval_ms = interval_ms
        self._timer.setInterval(interval_ms)

    def _set_source(self, source: str) -> None:
        self._collector.config.source = source
        self._reset_view()
        self.sourceChanged.emit(source)

    def _reset_view(self) -> None:
        self.tree.clear()
        self._group_items.clear()
        self._section_items.clear()
        self._reading_items.clear()

    def _edit_threshold(self) -> None:
        value, ok = QInputDialog.getDouble(
            self,
            "温度の警告しきい値",
            "この温度以上の行を赤で表示します (°C):",
            self._threshold,
            30.0,
            125.0,
            1,
        )
        if ok:
            self._threshold = float(value)
            # Force a repaint of every row on the next sample.
            for item in self._reading_items.values():
                item.setData(COL_NAME, _ROLE_ALARM, None)
            if self._last_sample is not None:
                self._render(self._last_sample)

    def _apply_always_on_top(self, enabled: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()  # re-applying window flags needs the window to be shown again

    def _show_diagnostics(self) -> None:
        sample = self._last_sample
        lines = [
            f"{APP_NAME} {__version__}",
            f"データ取得元: {self._collector.config.source}",
            f"hwmon root: {self._collector.config.hwmon_root}",
            f"drm root: {self._collector.config.drm_root}",
            f"更新間隔: {self._interval_ms} ms",
            f"警告しきい値: {self._threshold:.1f} °C",
            "",
        ]
        if sample is None:
            lines.append("まだサンプルを取得していません。")
        else:
            lines.append(f"有効なソース: {', '.join(sample.sources) or 'なし'}")
            lines.append(f"チップ: {len(sample.groups)} / センサー: {sample.sensor_count}")
            if sample.errors:
                lines.append("")
                lines.append("警告:")
                lines.extend(f"  • {error}" for error in sample.errors)
        QMessageBox.information(self, "診断情報", "\n".join(lines))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "バージョン情報",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "CPUID HWMonitor 風のハードウェアモニターです。<br>"
            "/sys/class/hwmon を直接読み取り、lm_sensors と nvidia-smi を"
            "補助的に利用します。<br><br>"
            "PySide6 (Qt for Python) 製。",
        )

    def _save_report(self) -> None:
        if self._last_sample is None:
            QMessageBox.information(self, APP_NAME, "保存できるデータがまだありません。")
            return
        default = os.path.join(
            os.path.expanduser("~"),
            _dt.datetime.now().strftime("sensorix-%Y%m%d-%H%M%S.txt"),
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "監視データを保存", default, "テキストファイル (*.txt);;すべてのファイル (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._render_report(self._last_sample))
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"保存に失敗しました:\n{exc}")
            return
        self.statusBar().showMessage(f"{path} に保存しました", 5000)

    def _render_report(self, sample: Sample) -> str:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"{APP_NAME} {__version__} - monitoring data", f"Generated: {stamp}", ""]
        width = 30
        for group in sample.groups:
            lines.append(f"{group.name}  [{group.detail}]")
            lines.append(
                f"  {'Name'.ljust(width)}{'Value':>12}{'Min':>12}{'Max':>12}{'Avg':>12}"
            )
            names = dict(group.sections)
            current = None
            for reading in group.readings:
                if reading.section != current:
                    current = reading.section
                    if current is not None:
                        lines.append(f"  [{names.get(current, current)}]")
                stats = reading.stats
                minimum = reading.format(stats.minimum) if stats else "-"
                maximum = reading.format(stats.maximum) if stats else "-"
                average = reading.format(stats.average) if stats else "-"
                indent = "    " if reading.section is not None else "  "
                label = reading.label[:width].ljust(width - (len(indent) - 2))
                lines.append(
                    f"{indent}{label}"
                    f"{reading.text:>12}{minimum:>12}{maximum:>12}{average:>12}"
                )
            lines.append("")
        if sample.errors:
            lines.append("Warnings:")
            lines.extend(f"  - {error}" for error in sample.errors)
            lines.append("")
        return "\n".join(lines)

    # -- settings / shutdown ----------------------------------------------
    def _restore_geometry(self) -> None:
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(640, 720)
        state = self._settings.value("window/columns")
        if state is not None:
            self.tree.header().restoreState(state)
        if self._settings.value("window/onTop", False, type=bool):
            self._on_top_action.setChecked(True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/columns", self.tree.header().saveState())
        self._settings.setValue("window/onTop", self._on_top_action.isChecked())
        self._settings.setValue("view/showChipNames", self._show_chips)
        try:
            self._timer.stop()
            self._thread.quit()
            if not self._thread.wait(5000):
                self._thread.terminate()
                self._thread.wait(1000)
        except RuntimeError:
            pass
        super().closeEvent(event)
