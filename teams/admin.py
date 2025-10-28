from django.contrib import admin, messages
from django.contrib.admin.sites import NotRegistered
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from django_otp.plugins.otp_totp.models import TOTPDevice as CoreTOTPDevice
from django_otp.plugins.otp_totp.admin import (
    TOTPDeviceAdmin as CoreTOTPDeviceAdmin,
)
from django_otp.models import VerifyNotAllowed
from awg.admin import PowerLeadAdmin as CorePowerLeadAdmin
from core.admin import (
    InviteLeadAdmin,
    UserAdmin as CoreUserAdmin,
    SecurityGroupAdmin,
    EmailInboxAdmin,
    EmailCollectorAdmin,
    ReleaseManagerAdmin,
    OdooProfileAdmin,
    GoogleCalendarProfileAdmin,
)
from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
)
from core.user_data import (
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    _fixture_path,
    _resolve_fixture_user,
    _user_allows_user_data,
)
from nodes.admin import EmailOutboxAdmin
from nodes.models import EmailOutbox as CoreEmailOutbox

from .forms import TOTPDeviceAdminForm, TOTPDeviceCalibrationActionForm
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
    TOTPDevice,
    GoogleCalendarProfile,
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


@admin.register(GoogleCalendarProfile)
class GoogleCalendarProfileAdminProxy(GoogleCalendarProfileAdmin):
    pass


try:
    admin.site.unregister(CoreTOTPDevice)
except NotRegistered:
    pass


@admin.register(TOTPDevice)
class TOTPDeviceAdminProxy(UserDatumAdminMixin, CoreTOTPDeviceAdmin):
    raw_id_fields = ()
    form = TOTPDeviceAdminForm
    action_form = TOTPDeviceCalibrationActionForm
    actions = tuple(CoreTOTPDeviceAdmin.actions or ()) + ("calibrate_device",)
    change_form_template = "admin/user_datum_change_form.html"

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj=obj)
        if fieldsets:
            identity_fields = list(fieldsets[0][1].get("fields", ()))
            if "issuer" not in identity_fields:
                try:
                    insert_at = identity_fields.index("name") + 1
                except ValueError:
                    insert_at = len(identity_fields)
                identity_fields.insert(insert_at, "issuer")
                fieldsets[0][1]["fields"] = identity_fields
        return fieldsets

    @admin.action(description=_("Test/calibrate selected device"))
    def calibrate_device(self, request, queryset):
        if request.POST.get("action") != "calibrate_device":
            return

        token = (request.POST.get("token") or "").strip()

        if queryset.count() != 1:
            self.message_user(
                request,
                _("Select exactly one device to calibrate."),
                level=messages.ERROR,
            )
            return

        if not token:
            self.message_user(
                request,
                _("Enter the current authenticator code to test the device."),
                level=messages.ERROR,
            )
            return

        device = queryset.first()

        allowed, data = device.verify_is_allowed()
        if not allowed:
            message = None
            if data:
                reason = data.get("reason")
                if reason == VerifyNotAllowed.N_FAILED_ATTEMPTS:
                    locked_until = data.get("locked_until")
                    if locked_until:
                        locked_until = timezone.localtime(locked_until)
                        locked_until_display = formats.date_format(
                            locked_until, "DATETIME_FORMAT"
                        )
                        message = _("Too many failed attempts. Try again after %(time)s.") % {
                            "time": locked_until_display
                        }
                    else:
                        message = _("Too many failed attempts. Try again later.")
                elif data.get("error_message"):
                    message = data["error_message"]
            if message is None:
                message = _("Verification is not allowed at this time.")
            self.message_user(request, message, level=messages.ERROR)
            return

        if device.verify_token(token):
            self.message_user(
                request,
                _("Token accepted. The device has been calibrated."),
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("Token rejected. The device was not calibrated."),
                level=messages.ERROR,
            )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        obj.is_seed_data = request.POST.get("_seed_datum") == "on"

        if getattr(self, "_skip_entity_user_datum", False):
            return

        target_user = _resolve_fixture_user(obj, request.user)
        allow_user_data = _user_allows_user_data(target_user)

        if request.POST.get("_user_datum") == "on":
            if allow_user_data:
                if not obj.is_user_data:
                    obj.is_user_data = True
                dump_user_fixture(obj, target_user)
                if target_user is None:
                    target_user = _resolve_fixture_user(obj, None)
                if target_user is not None:
                    path = _fixture_path(target_user, obj)
                    self.message_user(
                        request,
                        _("User datum saved to %(path)s") % {"path": path},
                    )
            else:
                if obj.is_user_data:
                    obj.is_user_data = False
                    delete_user_fixture(obj, target_user)
                self.message_user(
                    request,
                    _("User data is not available for this account."),
                    level=messages.WARNING,
                )
        elif obj.is_user_data:
            obj.is_user_data = False
            delete_user_fixture(obj, target_user)

