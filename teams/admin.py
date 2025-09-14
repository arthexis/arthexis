from django.contrib import admin
from django.contrib.admin.sites import NotRegistered

from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
    ChatProfile as CoreChatProfile,
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
    ChatProfileAdmin,
)
from awg.admin import PowerLeadAdmin
from nodes.admin import EmailOutboxAdmin

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
    ChatProfile,
)


for model in [
    CoreInviteLead,
    CoreSecurityGroup,
    CoreEmailInbox,
    CoreEmailCollector,
    CoreReleaseManager,
    CoreOdooProfile,
    CoreChatProfile,
    CoreUser,
    CoreEmailOutbox,
]:
    try:
        admin.site.unregister(model)
    except NotRegistered:
        pass


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


@admin.register(ChatProfile)
class ChatProfileAdminProxy(ChatProfileAdmin):
    pass
