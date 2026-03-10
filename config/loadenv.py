from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


def loadenv() -> None:
    """Load repository and persisted admin override `.env` files."""
    env_files = sorted(BASE_DIR.glob("*.env"))
    user_env_dir = BASE_DIR / "var" / "user_env"
    if user_env_dir.exists():
        env_files.extend(sorted(user_env_dir.glob("*.env")))

    for env_file in env_files:
        load_dotenv(env_file, override=False)
