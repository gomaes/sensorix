"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List

from . import APP_NAME, __version__
from .desktop import ICON_NAME
from .sensors.collector import (
    SOURCE_AUTO,
    SOURCE_HWMON,
    SOURCE_SENSORS,
    Collector,
    CollectorConfig,
)
from .sensors.cpu import DEFAULT_PROC_STAT
from .sensors.cputopo import DEFAULT_CPU_ROOT, DEFAULT_PMU_ROOT
from .sensors.devinfo import DEFAULT_CPUINFO, DEFAULT_DMI_ROOT
from .sensors.disk import DEFAULT_BLOCK_ROOT
from .sensors.hwmon import DEFAULT_DRM_ROOT, DEFAULT_HWMON_ROOT
from .sensors.net import DEFAULT_NET_ROOT
from .sensors.pciids import SEARCH_PATHS
from .sensors.model import Sample


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sensorix",
        description=f"{APP_NAME} - CPUID HWMonitor 風のハードウェアモニター",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="更新間隔 (秒, 既定: 1.0)",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=80.0,
        metavar="CELSIUS",
        help="この温度以上の行を赤で表示する (既定: 80)",
    )
    parser.add_argument(
        "-s",
        "--source",
        choices=[SOURCE_AUTO, SOURCE_HWMON, SOURCE_SENSORS],
        default=SOURCE_AUTO,
        help="センサーの取得元 (既定: auto = hwmon、駄目なら lm_sensors)",
    )
    parser.add_argument("--no-nvidia", action="store_true", help="nvidia-smi を使わない")
    parser.add_argument("--no-net", action="store_true", help="NIC の通信速度を表示しない")
    parser.add_argument("--no-disk", action="store_true", help="ディスクの転送速度を表示しない")
    parser.add_argument(
        "--no-cpu",
        action="store_true",
        help="CPU のコア別クロック・負荷率とコア種別のグループ分けを行わない",
    )
    parser.add_argument("--always-on-top", action="store_true", help="常に最前面で起動する")
    parser.add_argument(
        "--hwmon-root", default=DEFAULT_HWMON_ROOT, help=argparse.SUPPRESS
    )
    parser.add_argument("--drm-root", default=DEFAULT_DRM_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--net-root", default=DEFAULT_NET_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--block-root", default=DEFAULT_BLOCK_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--dmi-root", default=DEFAULT_DMI_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--cpuinfo", default=DEFAULT_CPUINFO, help=argparse.SUPPRESS)
    parser.add_argument("--pci-ids", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--cpu-root", default=DEFAULT_CPU_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--pmu-root", default=DEFAULT_PMU_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--proc-stat", default=DEFAULT_PROC_STAT, help=argparse.SUPPRESS)
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="GUI を起動せず、検出したセンサーを一覧表示して終了する",
    )
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="GUI を起動せず、端末上で更新し続ける (Ctrl+C で終了)",
    )
    return parser


def _config_from(args: argparse.Namespace) -> CollectorConfig:
    return CollectorConfig(
        hwmon_root=args.hwmon_root,
        drm_root=args.drm_root,
        net_root=args.net_root,
        block_root=args.block_root,
        dmi_root=args.dmi_root,
        cpuinfo=args.cpuinfo,
        pci_ids_paths=(args.pci_ids,) if args.pci_ids else tuple(SEARCH_PATHS),
        source=args.source,
        enable_nvidia=not args.no_nvidia,
        enable_net=not args.no_net,
        enable_disk=not args.no_disk,
        enable_cpu=not args.no_cpu,
        cpu_root=args.cpu_root,
        pmu_root=args.pmu_root,
        proc_stat=args.proc_stat,
    )


def format_sample(sample: Sample, threshold: float, color: bool) -> str:
    """Plain-text rendering used by --list and --watch."""
    red, dim, bold, off = ("\033[31m", "\033[90m", "\033[1m", "\033[0m") if color else ("",) * 4
    lines: List[str] = []
    header = f"{'Name'.ljust(28)}{'Value':>12}{'Min':>12}{'Max':>12}{'Avg':>12}"
    for group in sample.groups:
        lines.append(f"{bold}{group.name}{off}  {dim}[{group.detail}]{off}")
        lines.append(f"{dim}  {header}{off}")
        names = dict(group.sections)
        current = None
        for reading in group.readings:
            if reading.section != current:
                current = reading.section
                if current is not None:
                    lines.append(f"  {bold}{names.get(current, current)}{off}")
            stats = reading.stats
            minimum = reading.format(stats.minimum) if stats else "-"
            maximum = reading.format(stats.maximum) if stats else "-"
            average = reading.format(stats.average) if stats else "-"
            indent = "    " if reading.section is not None else "  "
            width = 30 - len(indent)
            row = (
                f"{indent}{reading.label[:width].ljust(width)}"
                f"{reading.text:>12}{minimum:>12}{maximum:>12}{average:>12}"
            )
            lines.append(f"{red}{row}{off}" if reading.is_alarm(threshold) else row)
        lines.append("")
    if sample.sources:
        lines.append(f"{dim}source: {' · '.join(sample.sources)}{off}")
    for error in sample.errors:
        lines.append(f"{red}警告: {error}{off}")
    return "\n".join(lines)


def run_headless(args: argparse.Namespace) -> int:
    collector = Collector(_config_from(args))
    color = sys.stdout.isatty()
    if not args.watch:
        sample = collector.sample()
        print(format_sample(sample, args.threshold, color))
        return 0 if sample.groups else 1
    interval = max(0.25, args.interval)
    try:
        while True:
            sample = collector.sample()
            if color:
                sys.stdout.write("\033[H\033[2J")
            print(format_sample(sample, args.threshold, color))
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def run_gui(args: argparse.Namespace) -> int:
    # Qt's xcb plugin takes the X11 WM_CLASS instance name from $RESOURCE_NAME,
    # and falls back to the basename of argv[0] - which is "python3" when the
    # app is started as `python -m sensorix`. KDE's task manager matches the
    # window against StartupWMClass in sensorix.desktop, so without this the
    # window cannot be pinned to the panel as Sensorix. Must be set before
    # QApplication is constructed, and regardless of how we were launched.
    os.environ.setdefault("RESOURCE_NAME", ICON_NAME)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "PySide6 が見つかりません。\n"
            "  uv sync      (uv を使う場合)\n"
            "  pip install -r requirements.txt\n"
            "のいずれかで依存関係をインストールしてください。\n"
            "GUI なしで確認するには `sensorix --list` が使えます。\n"
        )
        return 2

    from .ui import theme
    from .ui.icons import app_icon
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    # applicationName doubles as the X11 WM_CLASS, and desktopFileName as the
    # Wayland app_id; both must match sensorix.desktop for the desktop shell to
    # pair the window with its launcher icon.
    app.setApplicationName(ICON_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(ICON_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("sensorix")
    app.setWindowIcon(app_icon())
    theme.apply(app)

    window = MainWindow(
        collector=Collector(_config_from(args)),
        interval_ms=int(max(0.25, args.interval) * 1000),
        temp_threshold=args.threshold,
        always_on_top=args.always_on_top,
    )
    window.show()
    return app.exec()


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list or args.watch:
        return run_headless(args)
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
