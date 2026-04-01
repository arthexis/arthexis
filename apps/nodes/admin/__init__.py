from .email_outbox_admin import EmailOutboxAdmin
from .enrollment_admin import NodeEnrollmentAdmin, NodeEnrollmentEventAdmin
from .inlines import NodeFeatureAssignmentInline
from .migration_checkpoint_admin import NodeMigrationCheckpointAdmin
from .net_message_admin import NetMessageAdmin
from .node_admin import NodeAdmin
from .node_feature_admin import NodeFeatureAdmin
from .node_role_admin import NodeRoleAdmin
from .platform_admin import PlatformAdmin
from .upgrade_policy_admin import UpgradePolicyAdmin

__all__ = [
    "EmailOutboxAdmin",
    "NetMessageAdmin",
    "NodeEnrollmentAdmin",
    "NodeEnrollmentEventAdmin",
    "NodeAdmin",
    "NodeFeatureAdmin",
    "NodeMigrationCheckpointAdmin",
    "NodeRoleAdmin",
    "PlatformAdmin",
    "NodeFeatureAssignmentInline",
    "UpgradePolicyAdmin",
]
