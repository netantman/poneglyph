"""
Setup script for Poneglyph:
1. Creates a desktop shortcut (.lnk) to launch the webapp
2. Registers a Windows Task Scheduler task for daily DB backup
   (validate_db.py -> backup_db.py, plus on-logon catch-up)
3. (Future) Registers Task Scheduler tasks for scouting and cross-synthesis
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_SCRIPT = PROJECT_ROOT / "scripts" / "launch_webapp.pyw"
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_db.py"
VALIDATE_SCRIPT = PROJECT_ROOT / "scripts" / "validate_db.py"
BACKUP_WRAPPER = PROJECT_ROOT / "scripts" / "run_backup.cmd"

BACKUP_TASK_NAME = "Poneglyph Daily Backup"


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


def register_backup_task() -> None:
    """Create / replace the daily DB backup task in Windows Task Scheduler.

    Two triggers:
      - Daily at 03:00 (registers fine without admin)
      - On user logon (with --skip-if-recent) — REQUIRES admin to register.
        If the script isn't running elevated, this trigger is skipped with a
        warning and the daily trigger still goes through.
    """
    daily_ok = False
    logon_ok = False

    # Daily 03:00 trigger — point at the .cmd wrapper. The wrapper handles the
    # validate -> backup chain in shell-native syntax (avoids schtasks quoting hell).
    subprocess.run(
        ["schtasks", "/Delete", "/TN", BACKUP_TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    daily = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", BACKUP_TASK_NAME,
            "/TR", str(BACKUP_WRAPPER),
            "/SC", "DAILY",
            "/ST", "03:00",
            "/RL", "LIMITED",
            "/F",
        ],
        capture_output=True, text=True,
    )
    if daily.returncode == 0:
        daily_ok = True
    else:
        print(f"ERROR registering daily trigger: {daily.stderr.strip()}")

    # Logon catch-up — also via wrapper, with --skip-if-recent passed through.
    logon_task = f"{BACKUP_TASK_NAME} (Logon Catch-up)"
    subprocess.run(
        ["schtasks", "/Delete", "/TN", logon_task, "/F"],
        capture_output=True, text=True,
    )
    logon_cmd = f'{BACKUP_WRAPPER} --skip-if-recent'
    logon = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", logon_task,
            "/TR", logon_cmd,
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/F",
        ],
        capture_output=True, text=True,
    )
    if logon.returncode == 0:
        logon_ok = True
    elif "Access is denied" in (logon.stderr or "") or "denied" in (logon.stderr or "").lower():
        print(
            "NOTE: skipped logon catch-up trigger — ONLOGON tasks need admin to register.\n"
            "      Re-run this script from an elevated PowerShell to add it,\n"
            "      or skip it (the daily 03:00 task is the main protection)."
        )
    else:
        print(f"ERROR registering logon trigger: {logon.stderr.strip()}")

    print()
    print("Task Scheduler status:")
    print(f"  [{'OK ' if daily_ok else 'FAIL'}] '{BACKUP_TASK_NAME}'  (daily 03:00, validate -> backup)")
    print(f"  [{'OK ' if logon_ok else 'SKIP'}] '{logon_task}'  (on logon, --skip-if-recent)")
    print("Verify in Task Scheduler -> Task Scheduler Library.")


def main() -> None:
    print("=== Poneglyph Setup ===\n")
    create_desktop_shortcut()
    print()
    register_backup_task()
    print("\nSetup complete.")


if __name__ == "__main__":
    main()
