import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache()
def get_revision() -> str:
    """Return the current Git commit hash, or ``""`` when unavailable.

    The value is cached for the lifetime of the process to avoid repeated
    subprocess calls, but will be refreshed on each restart. If Git metadata
    cannot be read (for example when running from a packaged release), the
    function falls back to an empty string rather than raising an exception.
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
        # Gracefully degrade when Git metadata is inaccessible.
        return ""
