import os
import runpy
import sys


def main(argv=None):
    argv = argv or []
    os.environ.pop("DEBUGPY_LAUNCHER_PORT", None)
    if "PYTHONPATH" in os.environ:
        os.environ["PYTHONPATH"] = os.pathsep.join(
            p for p in os.environ["PYTHONPATH"].split(os.pathsep) if "debugpy" not in p
        )
    sys.argv = ["manage.py", *argv]
    runpy.run_path("manage.py", run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
