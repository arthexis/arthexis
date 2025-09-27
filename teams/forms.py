from django import forms
from django.conf import settings
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

from core.models import TOTPDeviceSettings
from django_otp.plugins.otp_totp.models import TOTPDevice


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
            settings_obj.save()
        elif settings_obj is not None:
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
        label=_("One-time password"),
        required=False,
        help_text=_(
            "Enter the current authenticator code when running the"
            " calibration action."
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        token = cleaned_data.get("token")
        if token is not None:
            cleaned_data["token"] = token.strip()
        return cleaned_data
