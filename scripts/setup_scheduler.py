"""
Setup script for Poneglyph:
1. Creates a desktop shortcut (.lnk) to launch the webapp
2. (Future) Registers Windows Task Scheduler tasks for scouting and cross-synthesis
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "launch_webapp.pyw"


def create_desktop_shortcut() -> None:
    """Create a .lnk shortcut on the user's Desktop to launch the webapp."""
    try:
        # pywin32 is needed for creating .lnk files on Windows
        import pythoncom  # noqa: F401
        from win32com.client import Dispatch
    except ImportError:
        print("ERROR: pywin32 is required to create desktop shortcuts.")
        print("Install it with: pip install pywin32")
        sys.exit(1)

    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        # OneDrive may redirect Desktop
        desktop = Path.home() / "OneDrive" / "Desktop"
    if not desktop.exists():
        print(f"ERROR: Could not find Desktop folder. Tried:")
        print(f"  {Path.home() / 'Desktop'}")
        print(f"  {Path.home() / 'OneDrive' / 'Desktop'}")
        sys.exit(1)

    shortcut_path = desktop / "Poneglyph.lnk"

    # Use python.exe — the launcher script uses CREATE_NO_WINDOW to hide the console
    pythonw = Path(sys.executable)

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.TargetPath = str(pythonw)
    shortcut.Arguments = str(LAUNCHER_SCRIPT)
    shortcut.WorkingDirectory = str(PROJECT_ROOT)
    shortcut.Description = "Launch Poneglyph research paper scouting webapp"
    icon_path = PROJECT_ROOT / "static" / "icon.ico"
    shortcut.IconLocation = str(icon_path) if icon_path.exists() else str(pythonw)
    shortcut.save()

    print(f"Desktop shortcut created: {shortcut_path}")
    print(f"  Target: {pythonw}")
    print(f"  Script: {LAUNCHER_SCRIPT}")


def setup_task_scheduler() -> None:
    """Register Windows Task Scheduler tasks. (Phase 7 — placeholder)"""
    print("\nTask Scheduler setup will be implemented in Phase 7.")
    print("  - Weekly scouting: scheduler_entry.py --mode scout")
    print("  - Monthly cross-synthesis: scheduler_entry.py --mode cross-synthesis")


def main() -> None:
    print("=== Poneglyph Setup ===\n")
    create_desktop_shortcut()
    setup_task_scheduler()
    print("\nSetup complete.")


if __name__ == "__main__":
    main()
