from django import forms
from django.conf import settings
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

from core.models import TOTPDeviceSettings
from core.widgets import OdooProductWidget
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import SlackBotProfile, TaskCategory


class TOTPDeviceAdminForm(forms.ModelForm):
    issuer = forms.CharField(
        label=_("Issuer"),
        required=False,
        help_text=_("Label shown in authenticator apps. Leave blank to use Arthexis."),
    )

    class Meta:
        model = TOTPDevice
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            settings_obj = self.instance.custom_settings
        except ObjectDoesNotExist:
            settings_obj = None
        if settings_obj is not None:
            self.fields["issuer"].initial = settings_obj.issuer
        default_issuer = getattr(settings, "OTP_TOTP_ISSUER", "Arthexis")
        self.fields["issuer"].widget.attrs.setdefault("placeholder", default_issuer)

    def _save_issuer(self, instance):
        issuer = (self.cleaned_data.get("issuer") or "").strip()
        try:
            settings_obj = instance.custom_settings
        except ObjectDoesNotExist:
            settings_obj = None
        if issuer:
            if settings_obj is None:
                settings_obj = TOTPDeviceSettings(device=instance)
            settings_obj.issuer = issuer
            if settings_obj.pk:
                settings_obj.save(
                    update_fields=["issuer", "is_seed_data", "is_user_data"]
                )
            else:
                settings_obj.save()
        elif settings_obj is not None:
            settings_obj.issuer = ""
            if settings_obj.is_seed_data or settings_obj.is_user_data:
                settings_obj.save(
                    update_fields=["issuer", "is_seed_data", "is_user_data"]
                )
            else:
                settings_obj.delete()

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self._save_issuer(instance)
        else:
            self._pending_instance = instance
        return instance

    def save_m2m(self):
        super().save_m2m()
        pending_instance = getattr(self, "_pending_instance", None)
        if pending_instance is not None:
            self._save_issuer(pending_instance)
            delattr(self, "_pending_instance")

class TOTPDeviceCalibrationActionForm(ActionForm):
    token = forms.CharField(
        label=_("OTP"),
        required=False,
        help_text=_(
            "Enter the current authenticator code when running the"
            " calibration action."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        token_field = self.fields["token"]
        token_field.widget.attrs.setdefault(
            "title", _("Enter your one-time password for testing")
        )
        existing_classes = token_field.widget.attrs.get("class", "")
        spacing_class = "totp-token-spacing"
        if spacing_class not in existing_classes.split():
            token_field.widget.attrs["class"] = (existing_classes + " " + spacing_class).strip()

    def clean(self):
        cleaned_data = super().clean()
        token = cleaned_data.get("token")
        if token is not None:
            cleaned_data["token"] = token.strip()
        return cleaned_data

    class Media:
        css = {
            "all": ("teams/css/totp_admin.css",)
        }


class SlackBotProfileAdminForm(forms.ModelForm):
    """Provide contextual help for Slack bot configuration fields."""

    class Meta:
        model = SlackBotProfile
        fields = "__all__"

    _help_text_overrides = {
        "node": _(
            "Node that owns this Slack chatbot. Defaults to the current node when adding a bot."
        ),
        "user": _(
            "Optional. Select a specific user to own the bot when it should not be tied to a node."
        ),
        "group": _(
            "Optional. Select a security group to share ownership when the bot should not be tied to a node."
        ),
        "team_id": _(
            "Slack workspace team identifier (starts with T). Copy it from the Slack app's Basic Information page."
        ),
        "bot_user_id": _(
            "Slack bot user identifier (starts with U or B). Slack fills this in after you test the connection, or copy it from the Install App settings."
        ),
        "bot_token": _(
            "Slack bot token used for authenticated API calls (begins with xoxb-). Store the OAuth token from the Slack app's Install App page."
        ),
        "signing_secret": _(
            "Slack signing secret used to verify incoming requests. Copy it from the Slack app's Basic Information page."
        ),
        "default_channels": _(
            "Channel identifiers where Net Messages should be posted. Provide a JSON array of channel IDs such as [\"C01ABCDE\"]."
        ),
        "is_enabled": _(
            "Uncheck to pause Slack announcements without deleting the credentials."
        ),
    }

    _placeholders = {
        "team_id": "T0123456789",
        "bot_user_id": "U0123456789",
        "bot_token": "xoxb-1234567890-ABCDEFGHIJKL",
        "signing_secret": "abcd1234efgh5678ijkl9012mnop3456",
        "default_channels": "[\"C01ABCDE\", \"C02FGHIJ\"]",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, help_text in self._help_text_overrides.items():
            field = self.fields.get(field_name)
            if field is not None:
                field.help_text = help_text
        for field_name, placeholder in self._placeholders.items():
            field = self.fields.get(field_name)
            if field is None:
                continue
            widget = field.widget
            if hasattr(widget, "attrs"):
                widget.attrs.setdefault("placeholder", placeholder)


class TaskCategoryAdminForm(forms.ModelForm):
    class Meta:
        model = TaskCategory
        fields = "__all__"
        widgets = {"odoo_product": OdooProductWidget}

