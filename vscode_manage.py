import os
import runpy
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    argv = list(argv or [])
    base_dir = Path(__file__).resolve().parent
    celery_enabled = (base_dir / "locks/celery.lck").exists()
    if "--celery" in argv:
        celery_enabled = True
        argv.remove("--celery")
    if "--no-celery" in argv:
        celery_enabled = False
        argv.remove("--no-celery")

    worker = beat = None
    is_runserver = bool(argv) and argv[0] == "runserver"
    is_debug_session = "DEBUGPY_LAUNCHER_PORT" in os.environ
    if is_runserver:
        os.environ["DEBUG"] = "1" if is_debug_session else "0"
        if "--noreload" not in argv:
            argv.insert(1, "--noreload")
    try:
        if celery_enabled:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "config",
                    "worker",
                    "-l",
                    "info",
                    "--concurrency=2",
                ]
            )
            beat = subprocess.Popen(
                [sys.executable, "-m", "celery", "-A", "config", "beat", "-l", "info"]
            )

        os.environ.pop("DEBUGPY_LAUNCHER_PORT", None)
        if "PYTHONPATH" in os.environ:
            os.environ["PYTHONPATH"] = os.pathsep.join(
                p
                for p in os.environ["PYTHONPATH"].split(os.pathsep)
                if "debugpy" not in p
            )
        sys.argv = ["manage.py", *argv]
        runpy.run_path("manage.py", run_name="__main__")
    finally:
        if worker:
            worker.terminate()
        if beat:
            beat.terminate()


if __name__ == "__main__":
    main(sys.argv[1:])
