"""Release-owned background task implementations."""

from .auto_upgrade import check_github_updates, verify_auto_upgrade_health

__all__ = ["check_github_updates", "verify_auto_upgrade_health"]
