from __future__ import annotations

from django.utils.translation import gettext as _

PYPI_REQUEST_TIMEOUT = 10

DIRTY_COMMIT_DEFAULT_MESSAGE = "chore: commit pending changes"

DIRTY_STATUS_LABELS = {
    "A": _("Added"),
    "C": _("Copied"),
    "D": _("Deleted"),
    "M": _("Modified"),
    "R": _("Renamed"),
    "U": _("Updated"),
    "??": _("Untracked"),
}

SENSITIVE_CONTEXT_KEYS = {"github_token"}

MAX_PYPI_PUBLISH_LOG_SIZE = 50000
