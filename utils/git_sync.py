import os
import subprocess
import sys
import threading
import time


def _restart_server() -> None:
    """Restart the current process with updated code."""
    os.execv(sys.executable, [sys.executable, *sys.argv])


def _sync_loop(interval: int) -> None:
    while True:
        try:
            subprocess.run(["git", "fetch"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
            status = subprocess.run(["git", "status", "-uno"], capture_output=True, text=True).stdout
            if "behind" in status and not dirty:
                result = subprocess.run(["git", "pull"], capture_output=True, text=True)
                if result.returncode == 0:
                    _restart_server()
        except Exception:
            pass
        time.sleep(interval)


def start_background_sync(interval: int = 60) -> None:
    """Start a background thread that keeps the repo up to date."""
    if os.environ.get("RUN_MAIN") != "true":
        return
    thread = threading.Thread(target=_sync_loop, args=(interval,), daemon=True)
    thread.start()
