from pathlib import Path
from dotenv import load_dotenv


ENV_DIR = Path(__file__).resolve().parent.parent / "envs"


def loadenv() -> None:
    """Load .env files from the repository's envs directory."""
    if not ENV_DIR.exists():
        return
    for env_file in sorted(ENV_DIR.glob("*.env")):
        load_dotenv(env_file, override=False)
