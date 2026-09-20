"""Application icon lookup.

Prefers the icon installed into the hicolor theme by install.sh, so the window,
the task switcher and the launcher entry all show the same image.  Falls back to
the copy bundled inside the package when running straight from a git clone.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QIcon

from ...desktop import ICON_NAME

_HERE = os.path.dirname(os.path.abspath(__file__))


def app_icon() -> QIcon:
    themed = QIcon.fromTheme(ICON_NAME)
    # A scalable-only theme entry reports no availableSizes(), so render a
    # pixmap to find out whether the theme really resolved the icon.
    if not themed.isNull() and not themed.pixmap(64, 64).isNull():
        return themed

    icon = QIcon()
    for filename in ("hwmonitor.png", "hwmonitor.svg"):
        path = os.path.join(_HERE, filename)
        if os.path.exists(path):
            icon.addFile(path)
    return icon
