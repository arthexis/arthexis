from django.contrib import admin

from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
    AssistantProfile as CoreAssistantProfile,
)
from nodes.models import EmailOutbox as CoreEmailOutbox
from core.admin import (
    InviteLeadAdmin,
    UserAdmin as CoreUserAdmin,
    SecurityGroupAdmin,
    EmailInboxAdmin,
    EmailCollectorAdmin,
    ReleaseManagerAdmin,
    OdooProfileAdmin,
    AssistantProfileAdmin,
)
from nodes.admin import EmailOutboxAdmin
from awg.admin import PowerLeadAdmin

from .models import (
    InviteLead,
    PowerLead,
    User,
    SecurityGroup,
    EmailInbox,
    EmailCollector,
    ReleaseManager,
    EmailOutbox,
    OdooProfile,
    AssistantProfile,
)


@admin.register(InviteLead)
class InviteLeadAdminProxy(InviteLeadAdmin):
    pass


@admin.register(PowerLead)
class PowerLeadAdminProxy(PowerLeadAdmin):
    pass


@admin.register(User)
class UserAdminProxy(CoreUserAdmin):
    pass


@admin.register(SecurityGroup)
class SecurityGroupAdminProxy(SecurityGroupAdmin):
    pass


@admin.register(EmailInbox)
class EmailInboxAdminProxy(EmailInboxAdmin):
    pass


@admin.register(EmailCollector)
class EmailCollectorAdminProxy(EmailCollectorAdmin):
    pass


@admin.register(ReleaseManager)
class ReleaseManagerAdminProxy(ReleaseManagerAdmin):
    pass


@admin.register(EmailOutbox)
class EmailOutboxAdminProxy(EmailOutboxAdmin):
    pass


@admin.register(OdooProfile)
class OdooProfileAdminProxy(OdooProfileAdmin):
    pass


@admin.register(AssistantProfile)
class AssistantProfileAdminProxy(AssistantProfileAdmin):
    pass
