from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.ocpp.models import Charger

from ..forms import NodeRoleMultipleChoiceField
from ..models import NetMessage, Node, NodeFeature, NodeRole


class NodeAdminForm(forms.ModelForm):
    class Meta:
        model = Node
        exclude = ("badge_color", "features")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ipv4_field = self.fields.get("ipv4_address")
        if ipv4_field:
            ipv4_field.widget = forms.TextInput()
            ipv4_field.help_text = _(
                "Enter IPv4 addresses separated by commas in priority order."
            )

    def clean_ipv4_address(self):
        value = self.cleaned_data.get("ipv4_address")
        if value in (None, ""):
            return ""
        serialized = Node.serialize_ipv4_addresses(value)
        if serialized is None:
            raise forms.ValidationError(
                _("Enter at least one valid, non-loopback IPv4 address or leave blank."),
            )
        return serialized


class SendNetMessageForm(forms.Form):
    subject = forms.CharField(
        label=_("Subject"),
        max_length=NetMessage._meta.get_field("subject").max_length,
        required=False,
    )
    body = forms.CharField(
        label=_("Body"),
        max_length=NetMessage._meta.get_field("body").max_length,
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    expires_at = forms.DateTimeField(
        label=_("Expires at"),
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text=_("Stop propagating and displaying after this time."),
    )

    def clean(self):
        cleaned = super().clean()
        subject = (cleaned.get("subject") or "").strip()
        body = (cleaned.get("body") or "").strip()
        if not subject and not body:
            raise forms.ValidationError(_("Enter a subject or body to send."))
        cleaned["subject"] = subject
        cleaned["body"] = body
        expires_at = cleaned.get("expires_at")
        if expires_at and timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(
                expires_at, timezone.get_current_timezone()
            )
        cleaned["expires_at"] = expires_at
        return cleaned


class DownloadFirmwareForm(forms.Form):
    def __init__(self, node: Node, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_queryset = Charger.objects.filter(
            node_origin=node, connector_id__isnull=True
        ).order_by("display_name", "charger_id")
        self.fields["charger"].queryset = base_queryset

    charger = forms.ModelChoiceField(
        label=_("Charge point"),
        queryset=Charger.objects.none(),
        help_text=_("Select the EVCS to request firmware from."),
    )
    vendor_id = forms.CharField(
        label=_("Vendor ID"),
        max_length=255,
        initial="org.openchargealliance.firmware",
        help_text=_("Vendor identifier included in the DataTransfer request."),
    )


class NodeRoleAdminForm(forms.ModelForm):
    nodes = forms.ModelMultipleChoiceField(
        queryset=Node.objects.all(),
        required=False,
        widget=FilteredSelectMultiple("Nodes", False),
    )

    class Meta:
        model = NodeRole
        fields = ("name", "acronym", "description", "nodes")

    class Media:
        css = {"all": ("nodes/css/noderole_admin.css",)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["nodes"].initial = self.instance.node_set.all()


class NodeFeatureAdminForm(forms.ModelForm):
    roles = NodeRoleMultipleChoiceField()

    class Meta:
        model = NodeFeature
        fields = "__all__"

    class Media:
        css = {"all": ("nodes/css/node_role_multiselect.css",)}


class QuickSendForm(forms.ModelForm):
    class Meta:
        model = NetMessage
        fields = [
            "subject",
            "body",
            "expires_at",
            "lcd_channel_type",
            "lcd_channel_num",
            "attachments",
            "filter_node",
            "filter_node_feature",
            "filter_node_role",
            "filter_current_relation",
            "filter_installed_version",
            "filter_installed_revision",
            "target_limit",
        ]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class NetMessageAdminForm(forms.ModelForm):
    class Meta:
        model = NetMessage
        fields = "__all__"
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}
