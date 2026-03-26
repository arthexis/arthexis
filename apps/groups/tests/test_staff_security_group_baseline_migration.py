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

