import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


def loadenv() -> None:
    """Load all .env files from the repository root."""
    if os.environ.get("ARTHEXIS_SKIP_ENV_LOAD") == "1":
        return

    is_test_run = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    for env_file in sorted(BASE_DIR.glob("*.env")):
        if is_test_run and env_file.name == "redis.env":
            continue
        load_dotenv(env_file, override=False)
