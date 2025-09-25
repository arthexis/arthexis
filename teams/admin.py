from django.contrib import admin
from django.contrib.admin.sites import NotRegistered

from django_otp.plugins.otp_totp.models import TOTPDevice as CoreTOTPDevice
from django_otp.plugins.otp_totp.admin import (
    TOTPDeviceAdmin as CoreTOTPDeviceAdmin,
)
from awg.admin import PowerLeadAdmin as CorePowerLeadAdmin
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
from nodes.admin import EmailOutboxAdmin
from nodes.models import EmailOutbox as CoreEmailOutbox

from .models import (
    InviteLead,
    User,
    SecurityGroup,
    EmailInbox,
    EmailCollector,
    ReleaseManager,
    EmailOutbox,
    PowerLead,
    OdooProfile,
    AssistantProfile,
    TOTPDevice,
)


try:
    admin.site.unregister(CoreReleaseManager)
except NotRegistered:
    pass


@admin.register(InviteLead)
class InviteLeadAdminProxy(InviteLeadAdmin):
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


@admin.register(PowerLead)
class PowerLeadAdminProxy(CorePowerLeadAdmin):
    pass


@admin.register(OdooProfile)
class OdooProfileAdminProxy(OdooProfileAdmin):
    pass


@admin.register(AssistantProfile)
class AssistantProfileAdminProxy(AssistantProfileAdmin):
    pass


try:
    admin.site.unregister(CoreTOTPDevice)
except NotRegistered:
    pass


@admin.register(TOTPDevice)
class TOTPDeviceAdminProxy(CoreTOTPDeviceAdmin):
    raw_id_fields = ()
