from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
    GoogleCalendarProfile as CoreGoogleCalendarProfile,
)
from awg.models import PowerLead as CorePowerLead
from django_otp.plugins.otp_totp.models import (
    TOTPDevice as CoreTOTPDevice,
)
from nodes.models import EmailOutbox as CoreEmailOutbox


class InviteLead(CoreInviteLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreInviteLead._meta.verbose_name
        verbose_name_plural = CoreInviteLead._meta.verbose_name_plural


class PowerLead(CorePowerLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CorePowerLead._meta.verbose_name
        verbose_name_plural = CorePowerLead._meta.verbose_name_plural


class User(CoreUser):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreUser._meta.verbose_name
        verbose_name_plural = CoreUser._meta.verbose_name_plural


class SecurityGroup(CoreSecurityGroup):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreSecurityGroup._meta.verbose_name
        verbose_name_plural = CoreSecurityGroup._meta.verbose_name_plural


class EmailInbox(CoreEmailInbox):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailInbox._meta.verbose_name
        verbose_name_plural = CoreEmailInbox._meta.verbose_name_plural


class EmailCollector(CoreEmailCollector):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailCollector._meta.verbose_name
        verbose_name_plural = CoreEmailCollector._meta.verbose_name_plural


class ReleaseManager(CoreReleaseManager):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreReleaseManager._meta.verbose_name
        verbose_name_plural = CoreReleaseManager._meta.verbose_name_plural


class EmailOutbox(CoreEmailOutbox):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailOutbox._meta.verbose_name
        verbose_name_plural = CoreEmailOutbox._meta.verbose_name_plural


class OdooProfile(CoreOdooProfile):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreOdooProfile._meta.verbose_name
        verbose_name_plural = CoreOdooProfile._meta.verbose_name_plural


class TOTPDevice(CoreTOTPDevice):
    supports_user_datum = True
    supports_seed_datum = True

    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreTOTPDevice._meta.verbose_name
        verbose_name_plural = CoreTOTPDevice._meta.verbose_name_plural


class GoogleCalendarProfile(CoreGoogleCalendarProfile):
    class Meta:
        proxy = True
        app_label = "django_celery_beat"
        verbose_name = CoreGoogleCalendarProfile._meta.verbose_name
        verbose_name_plural = CoreGoogleCalendarProfile._meta.verbose_name_plural


