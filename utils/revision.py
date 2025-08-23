import subprocess
from functools import lru_cache
from pathlib import Path

@lru_cache()
def get_revision() -> str:
    """Return the current Git commit hash.

    The value is cached for the lifetime of the process to avoid repeated
    subprocess calls, but will be refreshed on each restart.
    """
    try:
        repo_root = Path(__file__).resolve().parents[1]
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=repo_root,
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""
