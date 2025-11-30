from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.sites import NotRegistered
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.http.request import split_domain_port
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from urllib.parse import urlencode, urlparse, urlunparse
import contextlib
import ipaddress
import secrets
import requests
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _, ngettext
from django_object_actions import DjangoObjectActions

from django_otp.plugins.otp_totp.models import TOTPDevice as CoreTOTPDevice
from django_otp.plugins.otp_totp.admin import (
    TOTPDeviceAdmin as CoreTOTPDeviceAdmin,
)
from django_otp.models import VerifyNotAllowed
from apps.awg.admin import PowerLeadAdmin as CorePowerLeadAdmin
from apps.core.admin import (
    InviteLeadAdmin,
    SecurityGroupAdmin,
    EmailInboxAdmin,
    EmailCollectorAdmin,
    ReleaseManagerAdmin,
    OdooProfileAdmin,
    GoogleCalendarProfileAdmin,
)
from apps.core.models import (
    InviteLead as CoreInviteLead,
    SecurityGroup as CoreSecurityGroup,
    ReleaseManager as CoreReleaseManager,
)
from apps.crms.models import OdooProfile as CoreOdooProfile
from apps.core.user_data import (
    EntityModelAdmin,
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    _fixture_path,
    _resolve_fixture_user,
    _user_allows_user_data,
)
from apps.nodes.admin import EmailOutboxAdmin
from apps.nodes.models import Node

from .forms import (
    SlackBotProfileAdminForm,
    SlackBotWizardSetupForm,
    TOTPDeviceAdminForm,
    TOTPDeviceCalibrationActionForm,
    TaskCategoryAdminForm,
)
from .models import (
    InviteLead,
    SecurityGroup,
    EmailInbox,
    EmailCollector,
    ReleaseManager,
    EmailOutbox,
    PowerLead,
    OdooProfile,
    TOTPDevice,
    GoogleCalendarProfile,
    ManualTask,
    SlackBotProfile,
    TaskCategory,
)


try:
    admin.site.unregister(CoreReleaseManager)
except NotRegistered:
    pass


@admin.register(InviteLead)
class InviteLeadAdminProxy(InviteLeadAdmin):
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


@admin.register(SlackBotProfile)
class SlackBotProfileAdmin(DjangoObjectActions, EntityModelAdmin):
    WIZARD_SESSION_KEY = "slack_bot_wizard_config"
    DEFAULT_SCOPE = "commands,chat:write,chat:write.public"

    list_display = ("__str__", "team_id", "node", "is_enabled")
    list_filter = ("is_enabled",)
    search_fields = ("team_id", "bot_user_id", "node__hostname")
    raw_id_fields = ("node", "user", "group")
    changelist_actions = ["bot_creation_wizard"]
    form = SlackBotProfileAdminForm
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "node",
                    "user",
                    "group",
                    "team_id",
                    "bot_user_id",
                    "default_channels",
                    "is_enabled",
                )
            },
        ),
        (
            _("Credentials"),
            {
                "fields": (
                    "bot_token",
                    "signing_secret",
                )
            },
        ),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial = dict(initial) if initial else {}
        if "node" not in initial or not initial["node"]:
            current = getattr(request, "node", None)
            if current is not None:
                initial["node"] = getattr(current, "pk", current)
            else:
                local_node = Node.get_local()
                if local_node is not None:
                    initial["node"] = local_node.pk
        return initial

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "bot-creation-wizard/",
                self.admin_site.admin_view(self.bot_creation_wizard_view),
                name="teams_slackbotprofile_bot_creation_wizard",
            ),
            path(
                "bot-creation-callback/",
                self.admin_site.admin_view(self.bot_creation_callback_view),
                name="teams_slackbotprofile_bot_creation_callback",
            ),
        ]
        return custom + urls

    def bot_creation_wizard(self, request, queryset=None):
        return HttpResponseRedirect(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard")
        )

    bot_creation_wizard.label = _("Bot Creation Wizard")
    bot_creation_wizard.short_description = _("Bot Creation Wizard")

    def _is_ip_address(self, host: str) -> bool:
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _slack_callback_host(self, request):
        raw_host = ""
        port = ""
        try:
            raw_host = request.get_host()
            port = request.get_port()
        except Exception:  # pragma: no cover - defensive
            raw_host = ""
            port = ""

        domain, explicit_port = split_domain_port(raw_host)
        host = (domain or "").strip()
        port = (explicit_port or port or "").strip()

        if self._is_ip_address(host):
            node = getattr(request, "node", None) or Node.get_local()
            for candidate in (
                getattr(node, "network_hostname", ""),
                getattr(node, "hostname", ""),
            ):
                candidate = (candidate or "").strip()
                if candidate and self._is_domain_like(candidate):
                    host = candidate
                    break

        return host, port

    def _is_domain_like(self, host: str) -> bool:
        if not host:
            return False
        host = host.strip()
        if host.lower() == "localhost":
            return False
        if self._is_ip_address(host):
            return False
        return "." in host

    def _slack_callback_url(self, request):
        configured = getattr(settings, "SLACK_REDIRECT_URL", "") or ""
        configured = configured.strip()
        if configured:
            return configured

        host, port = self._slack_callback_host(request)
        try:
            callback_path = reverse("teams:slack-bot-callback")
        except NoReverseMatch:  # pragma: no cover - url configuration integrity
            callback_path = reverse("admin:teams_slackbotprofile_bot_creation_callback")
        scheme = "https" if request.is_secure() else "http"

        if host:
            netloc = host
            default_port = "443" if scheme == "https" else "80"
            port = port or ""
            if port and port != default_port:
                netloc = f"{host}:{port}"
            return urlunparse((scheme, netloc, callback_path, "", "", ""))

        return request.build_absolute_uri(callback_path)

    def _slack_oauth_settings(self, request):
        session_config = {}
        if request is not None:
            session_config = request.session.get(self.WIZARD_SESSION_KEY, {}) or {}
        client_id = session_config.get("client_id") or getattr(settings, "SLACK_CLIENT_ID", "") or ""
        client_secret = session_config.get("client_secret") or getattr(
            settings, "SLACK_CLIENT_SECRET", ""
        )
        signing_secret = session_config.get("signing_secret") or getattr(
            settings, "SLACK_SIGNING_SECRET", ""
        )
        scopes = session_config.get("scopes") or getattr(settings, "SLACK_BOT_SCOPES", "") or ""
        return (
            client_id.strip(),
            client_secret.strip(),
            signing_secret.strip(),
            scopes.strip(),
            session_config,
        )

    def _wizard_response(
        self,
        request,
        form,
        changelist_url,
        callback_url,
        callback_host_error=False,
    ):
        parsed = urlparse(callback_url)
        callback_host = parsed.hostname or ""
        return TemplateResponse(
            request,
            "admin/teams/slack_bot_wizard.html",
            {
                "title": _("Connect a Slack bot"),
                "opts": SlackBotProfile._meta,
                "form": form,
                "changelist_url": changelist_url,
                "default_scope": self.DEFAULT_SCOPE,
                "callback_url": callback_url,
                "callback_host": callback_host,
                "callback_host_error": callback_host_error,
                "disable_submit": callback_host_error,
            },
        )

    def _get_owner_kwargs(self, request):
        owner = getattr(request, "node", None) or Node.get_local()
        if owner is not None:
            return {"node": owner, "user": None, "group": None}
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            return {"user": user, "group": None, "node": None}
        return {}

    def bot_creation_wizard_view(self, request):
        if request.method != "POST":
            request.session.pop(self.WIZARD_SESSION_KEY, None)
            request.session.pop("slack_bot_wizard_state", None)

        (
            client_id,
            client_secret,
            signing_secret,
            scopes,
            session_config,
        ) = self._slack_oauth_settings(request)
        changelist_url = reverse("admin:teams_slackbotprofile_changelist")
        callback_url = self._slack_callback_url(request)
        callback_host_error = self._is_ip_address(urlparse(callback_url).hostname or "")

        if request.method == "POST":
            form = SlackBotWizardSetupForm(request.POST)
            if form.is_valid():
                request.session[self.WIZARD_SESSION_KEY] = form.cleaned_data
                client_id = form.cleaned_data.get("client_id")
                client_secret = form.cleaned_data.get("client_secret")
                signing_secret = form.cleaned_data.get("signing_secret")
                scopes = form.cleaned_data.get("scopes")
                callback_url = self._slack_callback_url(request)
                callback_host_error = self._is_ip_address(
                    urlparse(callback_url).hostname or ""
                )
            else:
                return self._wizard_response(
                    request,
                    form,
                    changelist_url,
                    callback_url,
                    callback_host_error,
                )
        if not (client_id and client_secret and signing_secret):
            form = SlackBotWizardSetupForm(
                initial={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "signing_secret": signing_secret,
                    "scopes": scopes or self.DEFAULT_SCOPE,
                }
            )
            return self._wizard_response(
                request,
                form,
                changelist_url,
                callback_url,
                callback_host_error,
            )

        if callback_host_error:
            initial_data = session_config or {"scopes": scopes or self.DEFAULT_SCOPE}
            form = SlackBotWizardSetupForm(initial=initial_data)
            return self._wizard_response(
                request,
                form,
                changelist_url,
                callback_url,
                callback_host_error,
            )

        redirect_uri = callback_url
        state = secrets.token_urlsafe(32)
        request.session["slack_bot_wizard_state"] = state
        scope_param = scopes or self.DEFAULT_SCOPE
        params = {
            "client_id": client_id,
            "scope": scope_param,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
        return HttpResponseRedirect(auth_url)

    def bot_creation_callback_view(self, request):
        changelist_url = reverse("admin:teams_slackbotprofile_changelist")
        session_state = request.session.pop("slack_bot_wizard_state", None)
        state = request.GET.get("state")
        if not session_state or not state or session_state != state:
            self.message_user(
                request,
                _("Slack authorization could not be validated. Please try again."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        error = request.GET.get("error")
        if error:
            self.message_user(
                request,
                _("Slack returned an error: %(error)s") % {"error": error},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        code = request.GET.get("code")
        if not code:
            self.message_user(
                request,
                _("Slack did not provide an authorization code."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        client_id, client_secret, signing_secret, scopes, _session_config = self._slack_oauth_settings(
            request
        )
        if not (client_id and client_secret and signing_secret):
            self.message_user(
                request,
                _("Slack OAuth is not configured."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        redirect_uri = self._slack_callback_url(request)
        response = None
        try:
            response = requests.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                timeout=10,
            )
            data = response.json()
        except Exception:
            data = None
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

        if not isinstance(data, dict) or not data.get("ok"):
            error_message = "unknown_error"
            if isinstance(data, dict):
                error_message = data.get("error") or error_message
            self.message_user(
                request,
                _("Slack authentication failed: %(error)s")
                % {"error": error_message},
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        bot_token = (data.get("access_token") or "").strip()
        bot_user_id = (data.get("bot_user_id") or "").strip().upper()
        team = data.get("team") or {}
        team_id = (team.get("id") or "").strip().upper()
        incoming = data.get("incoming_webhook") or {}
        channel_id = (incoming.get("channel_id") or "").strip()

        if not bot_token or not team_id:
            self.message_user(
                request,
                _("Slack did not return the required workspace details."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        owner_kwargs = self._get_owner_kwargs(request)
        if not owner_kwargs:
            self.message_user(
                request,
                _("Unable to determine an owner for the Slack bot."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        defaults = {
            "bot_token": bot_token,
            "bot_user_id": bot_user_id,
            "signing_secret": signing_secret,
            "is_enabled": True,
            "default_channels": [channel_id] if channel_id else [],
            **owner_kwargs,
        }

        bot, _created = SlackBotProfile.objects.update_or_create(
            team_id=team_id,
            defaults=defaults,
        )

        request.session.pop(self.WIZARD_SESSION_KEY, None)

        self.message_user(
            request,
            _("Slack bot connected for workspace %(workspace)s")
            % {"workspace": team_id or _("your workspace")},
        )
        return HttpResponseRedirect(
            reverse("admin:teams_slackbotprofile_change", args=[bot.pk])
        )


@admin.register(TaskCategory)
class TaskCategoryAdmin(EntityModelAdmin):
    form = TaskCategoryAdminForm
    list_display = (
        "name",
        "availability_label",
        "cost",
        "default_duration",
        "manager",
        "requestor_group",
        "assigned_group",
    )
    list_filter = (
        "availability",
        "requestor_group",
        "assigned_group",
        "manager",
    )
    search_fields = ("name", "description")
    raw_id_fields = ("requestor_group", "assigned_group", "manager")
    filter_horizontal = ("odoo_products",)
    fieldsets = (
        (None, {"fields": ("name", "description", "image")}),
        (
            _("Fulfillment"),
            {
                "fields": (
                    "availability",
                    "cost",
                    "default_duration",
                    "odoo_products",
                )
            },
        ),
        (
            _("Routing"),
            {"fields": ("requestor_group", "assigned_group", "manager")},
        ),
    )


@admin.register(ManualTask)
class ManualTaskAdmin(EntityModelAdmin):
    list_display = (
        "category",
        "assigned_user",
        "assigned_group",
        "manager",
        "node",
        "location",
        "scheduled_start",
        "scheduled_end",
        "enable_notifications",
    )
    list_filter = ("node", "location", "enable_notifications", "category")
    search_fields = (
        "description",
        "node__hostname",
        "location__name",
        "assigned_user__username",
        "assigned_user__email",
        "assigned_group__name",
        "manager__username",
        "category__name",
    )
    raw_id_fields = (
        "node",
        "location",
        "assigned_user",
        "assigned_group",
        "manager",
    )
    filter_horizontal = ("odoo_products",)
    date_hierarchy = "scheduled_start"
    actions = ("make_cp_reservations",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "category",
                    "description",
                    "odoo_products",
                    "duration",
                    "assigned_user",
                    "assigned_group",
                    "manager",
                    "enable_notifications",
                )
            },
        ),
        (
            _("Scope"),
            {
                "fields": (
                    "node",
                    "location",
                )
            },
        ),
        (
            _("Schedule"),
            {"fields": ("scheduled_start", "scheduled_end")},
        ),
    )

    @admin.action(description=_("Make Reservation at CP"))
    def make_cp_reservations(self, request, queryset):
        success_count = 0
        for task in queryset:
            try:
                task.create_cp_reservation()
            except ValidationError as exc:
                for message in self._normalize_validation_error(exc):
                    self.message_user(
                        request,
                        _("%(task)s: %(message)s")
                        % {"task": task, "message": message},
                        level=messages.WARNING,
                    )
            except Exception as exc:  # pragma: no cover - defensive guard
                self.message_user(
                    request,
                    _("%(task)s: %(error)s")
                    % {"task": task, "error": str(exc)},
                    level=messages.ERROR,
                )
            else:
                success_count += 1
        if success_count:
            message = ngettext(
                "Created %(count)d reservation.",
                "Created %(count)d reservations.",
                success_count,
            ) % {"count": success_count}
            self.message_user(request, message, level=messages.SUCCESS)

    @staticmethod
    def _normalize_validation_error(error: ValidationError) -> list[str]:
        messages_list: list[str] = []
        if error.message_dict:
            for field, values in error.message_dict.items():
                for value in values:
                    if field == "__all__":
                        messages_list.append(str(value))
                    else:
                        messages_list.append(f"{field}: {value}")
        elif error.messages:
            messages_list.extend(str(value) for value in error.messages)
        else:
            messages_list.append(str(error))
        return messages_list


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
            if "allow_without_password" not in identity_fields:
                try:
                    issuer_index = identity_fields.index("issuer") + 1
                except ValueError:
                    issuer_index = len(identity_fields)
                identity_fields.insert(issuer_index, "allow_without_password")
            if "security_group" not in identity_fields:
                try:
                    password_index = identity_fields.index("allow_without_password") + 1
                except ValueError:
                    password_index = len(identity_fields)
                identity_fields.insert(password_index, "security_group")
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

