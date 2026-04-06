"""Named internal action registry for supported dashboard links and endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class InternalActionSpec:
    """Stable contract for a supported internal action.

    Parameters:
        name: Stable identifier stored in database rows.
        label: Default UI label for the action.
        description: Human-readable description shown in admin/task lists.
        admin_url_name: Django URL name for the internal destination.
        method: HTTP method used by dashboard rendering.
        is_discover: Whether the action should be styled as a discover action.
    """

    name: str
    label: str
    description: str
    admin_url_name: str
    method: str = "get"
    is_discover: bool = False


INTERNAL_ACTION_SPECS: tuple[InternalActionSpec, ...] = (
    InternalActionSpec(
        name="config",
        label="Config",
        description="Open configuration shortcuts.",
        admin_url_name="admin:config",
    ),
    InternalActionSpec(
        name="data",
        label="Data",
        description="Manage personal admin data and preferences.",
        admin_url_name="admin:user_data",
    ),
    InternalActionSpec(
        name="discover",
        label="Discover",
        description="Run node and integration discovery tools.",
        admin_url_name="admin:nodes_nodefeature_discover",
        is_discover=True,
    ),
    InternalActionSpec(
        name="environment",
        label="Environment",
        description="Inspect deployment environment details.",
        admin_url_name="admin:environment",
    ),
    InternalActionSpec(
        name="groups",
        label="Groups",
        description="Browse the current user's security groups.",
        admin_url_name="actions-api-security-groups",
    ),
    InternalActionSpec(
        name="imager",
        label="Imager",
        description="Open the Raspberry Pi image wizard.",
        admin_url_name="admin:imager_raspberrypiimageartifact_create_rpi_image",
    ),
    InternalActionSpec(
        name="logs",
        label="Logs",
        description="Browse system and application logs.",
        admin_url_name="admin:log_viewer",
    ),
    InternalActionSpec(
        name="reports",
        label="Reports",
        description="Run system reports and provide query parameters.",
        admin_url_name="admin:system-reports",
    ),
    InternalActionSpec(
        name="rules",
        label="Rules",
        description="Review dashboard rule evaluation outcomes.",
        admin_url_name="admin:system-dashboard-rules-report",
    ),
    InternalActionSpec(
        name="seed",
        label="Seed",
        description="Load baseline data into the system.",
        admin_url_name="admin:seed_data",
    ),
    InternalActionSpec(
        name="sigil",
        label="Sigil",
        description="Build and inspect sigils.",
        admin_url_name="admin:sigil_builder",
    ),
    InternalActionSpec(
        name="system",
        label="System",
        description="Inspect system details and service controls.",
        admin_url_name="admin:system-details",
    ),
    InternalActionSpec(
        name="tasks",
        label="Tasks",
        description="Open the task panels overview and toggles.",
        admin_url_name="admin:system",
    ),
    InternalActionSpec(
        name="upgrade",
        label="Upgrade",
        description="View upgrade status and run upgrade checks.",
        admin_url_name="admin:system-upgrade-report",
    ),
)

_INTERNAL_ACTION_SPEC_MAP = {spec.name: spec for spec in INTERNAL_ACTION_SPECS}


def get_internal_action_spec(action_name: str) -> InternalActionSpec | None:
    """Return the registered specification for a stored internal action name.

    Parameters:
        action_name: Stable registry key stored in action/task rows.

    Returns:
        Matching :class:`InternalActionSpec`, or ``None`` when unknown.
    """

    return _INTERNAL_ACTION_SPEC_MAP.get(action_name)


def get_internal_action_choices() -> list[tuple[str, str]]:
    """Return model-field choices for supported internal actions.

    Returns:
        List of ``(name, label)`` tuples suitable for Django field choices.
    """

    return [(spec.name, spec.label) for spec in INTERNAL_ACTION_SPECS]


def resolve_internal_action_url(action_name: str) -> str:
    """Resolve a registered internal action into a concrete URL.

    Parameters:
        action_name: Stable registry key stored in action/task rows.

    Returns:
        The resolved URL, or an empty string when the action is unknown or unroutable.
    """

    spec = get_internal_action_spec(action_name)
    if spec is None:
        return ""
    try:
        return reverse(spec.admin_url_name)
    except NoReverseMatch:
        return ""
