from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from apps.groups.models import SecurityGroup
from apps.modules.models import Module
from apps.sites.models.admin_badge import AdminBadge
from apps.widgets.models import Widget, WidgetProfile, WidgetZone

migration_module = import_module("apps.groups.migrations.0006_staff_security_group_baseline")
seed_staff_security_groups = migration_module.seed_staff_security_groups
unseed_staff_security_groups = migration_module.unseed_staff_security_groups


class StaffSecurityGroupBaselineMigrationTests(TestCase):
    """Regression coverage for migrating legacy staff security-group relations."""

    def _schema_editor(self):
        """Return a minimal schema editor stub exposing the active database alias."""

        return SimpleNamespace(connection=SimpleNamespace(alias=connection.alias))

    def test_seed_repoints_legacy_relations_before_deleting_legacy_groups(self):
        """Forward migration should preserve FK and M2M relations tied to legacy groups."""

        user = get_user_model().objects.create_user(username="operator")
        legacy_group = SecurityGroup.objects.create(name="Charge Station Manager")
        user.groups.add(legacy_group)
        module = Module.objects.create(path="/legacy/", security_group=legacy_group)
        badge = AdminBadge.objects.create(
            slug="legacy-badge",
            name="Legacy badge",
            label="Legacy",
            value_query_path="apps.core.good.get_version",
            group=legacy_group,
        )
        zone = WidgetZone.objects.create(slug="sidebar", name="Sidebar")
        widget = Widget.objects.create(
            slug="legacy-widget",
            name="Legacy Widget",
            zone=zone,
            template_name="widgets/tests/sample.html",
            renderer_path="apps.widgets.tests.test_services.render_zone_widgets",
        )
        WidgetProfile.objects.create(widget=widget, group=legacy_group, is_enabled=True)

        schema_editor = self._schema_editor()
        seed_staff_security_groups(global_apps, schema_editor)

        canonical_group = SecurityGroup.objects.get(name="Network Operator")
        module.refresh_from_db()
        badge.refresh_from_db()
        widget_profile = WidgetProfile.objects.get(widget=widget)

        assert not SecurityGroup.objects.filter(name="Charge Station Manager").exists()
        assert user.groups.filter(pk=canonical_group.pk).exists()
        assert module.security_group == canonical_group
        assert badge.group == canonical_group
        assert widget_profile.group_id == canonical_group.pk

    def test_unseed_restores_legacy_groups_and_relations(self):
        """Reverse migration should recreate legacy groups and move data back to them."""

        user = get_user_model().objects.create_user(username="external")
        site_operator = SecurityGroup.objects.create(name="Site Operator")
        canonical_group = SecurityGroup.objects.create(name="External Agent")
        user.groups.add(canonical_group)
        module = Module.objects.create(path="/external/", security_group=canonical_group)
        badge = AdminBadge.objects.create(
            slug="external-badge",
            name="External badge",
            label="External",
            value_query_path="apps.core.good.get_version",
            group=canonical_group,
        )
        zone = WidgetZone.objects.create(slug="application", name="Application")
        widget = Widget.objects.create(
            slug="external-widget",
            name="External Widget",
            zone=zone,
            template_name="widgets/tests/sample.html",
            renderer_path="apps.widgets.tests.test_services.render_zone_widgets",
        )
        WidgetProfile.objects.create(widget=widget, group=canonical_group, is_enabled=True)

        schema_editor = self._schema_editor()
        unseed_staff_security_groups(global_apps, schema_editor)

        legacy_group = SecurityGroup.objects.get(name="Odoo User")
        module.refresh_from_db()
        badge.refresh_from_db()
        widget_profile = WidgetProfile.objects.get(widget=widget)

        assert not SecurityGroup.objects.filter(name="External Agent").exists()
        assert SecurityGroup.objects.filter(pk=site_operator.pk).exists()
        assert user.groups.filter(pk=legacy_group.pk).exists()
        assert module.security_group == legacy_group
        assert badge.group == legacy_group
        assert widget_profile.group_id == legacy_group.pk
