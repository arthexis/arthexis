from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


def loadenv() -> None:
    """Load all .env files from the repository root."""
    for env_file in sorted(BASE_DIR.glob("*.env")):
        load_dotenv(env_file, override=False)
