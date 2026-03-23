"""Regression coverage for the Staff Chat Bridge metadata refresh migration."""

from __future__ import annotations

from importlib import import_module

migration_0050 = import_module(
    "apps.features.migrations.0050_refresh_staff_chat_bridge_suite_feature"
)


class RecordingApplicationManager:
    """Capture application lookups requested by the migration helper."""

    def __init__(self) -> None:
        """Initialize the application manager recorder.

        Returns:
            None.
        """

        self.last_get_or_create_kwargs: dict[str, object] | None = None
        self.application = object()

    def get_or_create(self, **kwargs):
        """Record the application lookup payload.

        Parameters:
            **kwargs: Keyword arguments supplied to ``get_or_create``.

        Returns:
            tuple[object, bool]: Minimal placeholder application and created flag.
        """

        self.last_get_or_create_kwargs = kwargs
        return self.application, True


class RecordingFeatureManager:
    """Capture feature updates issued by the migration helper."""

    def __init__(self) -> None:
        """Initialize the feature manager recorder.

        Returns:
            None.
        """

        self.last_update_or_create_kwargs: dict[str, object] | None = None

    def update_or_create(self, **kwargs):
        """Record the feature update payload.

        Parameters:
            **kwargs: Keyword arguments supplied to ``update_or_create``.

        Returns:
            tuple[object, bool]: Minimal placeholder feature and created flag.
        """

        self.last_update_or_create_kwargs = kwargs
        return object(), True


def test_0050_reverse_restores_previous_staff_chat_bridge_metadata() -> None:
    """Regression: rolling back 0050 should restore the pre-refresh metadata payload."""

    application_manager = RecordingApplicationManager()
    feature_manager = RecordingFeatureManager()

    migration_0050._update_feature_from_fields(
        feature_manager=feature_manager,
        application_manager=application_manager,
        fields=migration_0050.REVERSE_FIXTURE_FIELDS,
    )

    assert application_manager.last_get_or_create_kwargs == {
        "name": "sites",
        "defaults": {"description": ""},
    }
    assert feature_manager.last_update_or_create_kwargs == {
        "slug": migration_0050.FEATURE_SLUG,
        "defaults": {
            "display": "Staff Chat Bridge",
            "summary": (
                "Gates staff-facing chat bridge UI wiring for site and admin chat widgets."
            ),
            "is_enabled": True,
            "main_app": application_manager.application,
            "node_feature": None,
            "admin_requirements": (
                "Admin base template should only render the chat widget when this suite "
                "feature is enabled."
            ),
            "public_requirements": (
                "Public base template should only render the chat widget when this suite "
                "feature is enabled."
            ),
            "service_requirements": (
                "No additional backend services beyond configured pages chat socket path."
            ),
            "admin_views": ["admin:index"],
            "public_views": ["pages:index"],
            "service_views": ["settings:PAGES_CHAT_SOCKET_PATH"],
            "code_locations": [
                "apps/sites/context_processors.py",
                "apps/sites/templates/pages/base.html",
                "apps/sites/templates/admin/base_site.html",
            ],
            "protocol_coverage": {},
            "source": "mainstream",
            "is_seed_data": True,
            "is_deleted": False,
        },
    }
