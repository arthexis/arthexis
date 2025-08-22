import subprocess
from functools import lru_cache

@lru_cache()
def get_revision() -> str:
    """Return the current Git commit hash.

    The value is cached for the lifetime of the process to avoid repeated
    subprocess calls, but will be refreshed on each restart.
    """
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""
