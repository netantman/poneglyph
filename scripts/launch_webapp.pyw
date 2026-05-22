"""
Poneglyph webapp launcher — double-click to start server and open browser.

Idempotent: if the server is already running, just opens the browser.
If the port is stuck (TIME_WAIT / stale process), kills it first.
"""

import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

# Resolve project root (one level up from scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Conda env Python — always use the poneglyph environment
CONDA_PYTHON = Path(r"C:\Users\zhong\anaconda3\envs\poneglyph\python.exe")


def _port_is_listening() -> bool:
    """Check if something is actively listening on the port."""
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            return True
    except OSError:
        return False


def _kill_stale_process_on_port() -> None:
    """On Windows, find and kill any process holding the port."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            if f":{PORT}" in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid > 0:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
        # Wait for port to clear
        for _ in range(10):
            time.sleep(0.5)
            if not _port_is_listening():
                return
    except Exception:
        pass


def main() -> None:
    # Always kill existing server so we restart with latest code
    if _port_is_listening():
        _kill_stale_process_on_port()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    # Use conda env Python; fall back to current interpreter if env missing
    python = str(CONDA_PYTHON) if CONDA_PYTHON.exists() else sys.executable

    # Start uvicorn as a subprocess, logging to logs/server.log
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = open(log_dir / "server.log", "a", encoding="utf-8")

    server = subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            "poneglyph.app:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Wait until the server is actually accepting connections (up to 30s)
    for _ in range(60):
        time.sleep(0.5)
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                break
        except OSError:
            if server.poll() is not None:
                sys.exit(1)

    # Open browser
    webbrowser.open(URL)

    try:
        server.wait()
    except KeyboardInterrupt:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=5)


if __name__ == "__main__":
    main()
