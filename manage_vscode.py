import os
import sys
from pathlib import Path


def main() -> None:
    os.environ.pop("DEBUGPY_LAUNCHER_PORT", None)
    if "PYTHONPATH" in os.environ:
        os.environ["PYTHONPATH"] = os.pathsep.join(
            p for p in os.environ["PYTHONPATH"].split(os.pathsep) if "debugpy" not in p
        )
    manage = Path(__file__).resolve().parent / "manage.py"
    os.execv(sys.executable, [sys.executable, str(manage), *sys.argv[1:]])


if __name__ == "__main__":
    main()
