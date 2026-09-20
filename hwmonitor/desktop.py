"""Constants shared between the application and its desktop integration.

`ICON_NAME` has to stay in sync with three things at once:

* the basename of `desktop/hwmonitor.desktop.in` (so Wayland's app_id matches),
* `StartupWMClass` inside that file (so X11's WM_CLASS matches), and
* the installed icon name under `share/icons/hicolor/*/apps/`.

Importing it from here keeps that contract in one place and lets the CLI set
$RESOURCE_NAME without dragging in Qt.
"""

from __future__ import annotations

ICON_NAME = "hwmonitor"
DESKTOP_FILE = f"{ICON_NAME}.desktop"
