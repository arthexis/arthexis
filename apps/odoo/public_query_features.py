from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.features.utils import is_suite_feature_enabled

ODOO_PUBLIC_QUERY_EXECUTION_SECURE_MODE_FEATURE_SLUG = (
    "odoo-public-query-execution-secure-mode"
)
PUBLIC_QUERY_EXECUTION_RESTRICTION_MESSAGE = _(
    "Execution is restricted to authenticated staff users. Public pages remain metadata-only previews."
)


def is_public_query_execution_secure_mode_enabled(*, default: bool = False) -> bool:
    """Return whether secure-mode execution policy is enabled for public queries."""

    return is_suite_feature_enabled(
        ODOO_PUBLIC_QUERY_EXECUTION_SECURE_MODE_FEATURE_SLUG,
        default=default,
    )


__all__ = [
    "ODOO_PUBLIC_QUERY_EXECUTION_SECURE_MODE_FEATURE_SLUG",
    "PUBLIC_QUERY_EXECUTION_RESTRICTION_MESSAGE",
    "is_public_query_execution_secure_mode_enabled",
]
