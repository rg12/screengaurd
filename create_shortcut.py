"""Create launcher shortcuts for the packaged app.

Makes a "Launch Screengaurd" shortcut inside the project folder (so you can open
the folder and click to start) plus the desktop copy.
"""

import os

import winshell
from win32com.client import Dispatch

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(PROJECT_DIR, "dist", "tray_app.exe")
SHORTCUT_NAME = "Launch Screengaurd.lnk"


def create_shortcut(path, target):
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(path)
    shortcut.Targetpath = target
    shortcut.WorkingDirectory = os.path.dirname(target)
    shortcut.Description = "Start the Screengaurd tray app"
    shortcut.save()
    return path


def main():
    if not os.path.exists(TARGET):
        raise SystemExit(
            f"{TARGET} not found. Build it first:\n"
            "    pyinstaller --noconfirm --onefile --windowed --name tray_app tray_app.py"
        )

    created = [
        create_shortcut(os.path.join(PROJECT_DIR, SHORTCUT_NAME), TARGET),
        create_shortcut(os.path.join(winshell.desktop(), SHORTCUT_NAME), TARGET),
    ]
    for path in created:
        print(f"Created {path}")


if __name__ == "__main__":
    main()
