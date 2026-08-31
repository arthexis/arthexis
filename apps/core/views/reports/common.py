from __future__ import annotations

from django.utils.translation import gettext as _

from apps.release.publishing.constants import SENSITIVE_CONTEXT_KEYS

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

MAX_PYPI_PUBLISH_LOG_SIZE = 50000
