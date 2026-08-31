from django.apps import apps as django_apps

from .enrollment_admin import NodeEnrollmentAdmin, NodeEnrollmentEventAdmin
from .inlines import NodeFeatureAssignmentInline
from .migration_checkpoint_admin import NodeMigrationCheckpointAdmin
from .net_message_admin import NetMessageAdmin
from .node_admin import NodeAdmin
from .node_feature_admin import NodeFeatureAdmin
from .node_role_admin import NodeRoleAdmin
from .platform_admin import PlatformAdmin
from .remote_upgrade_admin import RemoteUpgradeRequestAdmin
from .upgrade_policy_admin import UpgradePolicyAdmin

if django_apps.is_installed("apps.emails"):
    from .email_outbox_admin import EmailOutboxAdmin
else:
    EmailOutboxAdmin = None

__all__ = [
    "EmailOutboxAdmin",
    "NetMessageAdmin",
    "NodeAdmin",
    "NodeEnrollmentAdmin",
    "NodeEnrollmentEventAdmin",
    "NodeFeatureAdmin",
    "NodeFeatureAssignmentInline",
    "NodeMigrationCheckpointAdmin",
    "NodeRoleAdmin",
    "PlatformAdmin",
    "RemoteUpgradeRequestAdmin",
    "UpgradePolicyAdmin",
]
