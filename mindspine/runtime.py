from __future__ import annotations

import os
import sys


def get_app_root() -> str:
    """Writable application root.

    In a PyInstaller one-folder build this is the directory that contains the
    exe. Runtime data such as uploads and generated reports should live here.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_root() -> str:
    """Read-only bundled resource root.

    PyInstaller stores collected data files under sys._MEIPASS for one-folder
    builds. Source runs use the project root.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return get_app_root()
