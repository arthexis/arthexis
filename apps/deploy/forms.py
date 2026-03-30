from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.aws.forms import BaseLightsailFetchForm


class LightsailSetupForm(BaseLightsailFetchForm):
    """Admin wizard form for registering existing/new Lightsail deploy targets."""

    skip_create = forms.BooleanField(
        label=_("Use existing instance"),
        required=False,
        initial=True,
        help_text=_("Leave enabled to register an existing instance without creating a new one."),
    )
    blueprint_id = forms.CharField(
        label=_("Blueprint ID"),
        required=False,
        help_text=_("Required only when creating a new Lightsail instance."),
    )
    bundle_id = forms.CharField(
        label=_("Bundle ID"),
        required=False,
        help_text=_("Required only when creating a new Lightsail instance."),
    )
    key_pair_name = forms.CharField(
        label=_("Key pair name"),
        required=False,
    )
    availability_zone = forms.CharField(
        label=_("Availability zone"),
        required=False,
    )
    deploy_instance_name = forms.CharField(label=_("Deploy instance name"), initial="main")
    install_dir = forms.CharField(
        label=_("Install directory"),
        required=False,
        help_text=_("Defaults to /srv/<instance-name> when left blank."),
    )
    service_name = forms.CharField(
        label=_("Service name"),
        required=False,
        help_text=_("Defaults to arthexis-<instance-name> when left blank."),
    )
    branch = forms.CharField(label=_("Branch"), required=False, initial="main")
    ocpp_port = forms.IntegerField(label=_("OCPP port"), initial=9000, min_value=1, max_value=65535)
    ssh_user = forms.CharField(label=_("SSH user"), initial="ubuntu")
    ssh_port = forms.IntegerField(label=_("SSH port"), initial=22, min_value=1, max_value=65535)
    admin_url = forms.CharField(label=_("Admin URL"), required=False)
    env_file = forms.CharField(label=_("Env file"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = _("Instance name")

    def clean(self):
        data = super().clean()
        instance_name = (data.get("name") or "").strip()
        skip_create = data.get("skip_create", False)
        blueprint_id = (data.get("blueprint_id") or "").strip()
        bundle_id = (data.get("bundle_id") or "").strip()
        if not skip_create and (not blueprint_id or not bundle_id):
            raise forms.ValidationError(
                _("Blueprint ID and Bundle ID are required when creating a new instance."),
                code="missing-create-options",
            )
        if instance_name:
            data["install_dir"] = str(data.get("install_dir") or "").strip() or f"/srv/{instance_name}"
            data["service_name"] = str(data.get("service_name") or "").strip() or f"arthexis-{instance_name}"
        else:
            data["install_dir"] = str(data.get("install_dir") or "").strip()
            data["service_name"] = str(data.get("service_name") or "").strip()
        data["branch"] = str(data.get("branch") or "main").strip() or "main"
        return data
