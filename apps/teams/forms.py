from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SlackBotProfile


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


class SlackBotWizardSetupForm(forms.Form):
    client_id = forms.CharField(
        label=_("Slack Client ID"),
        help_text=_("Copy from your Slack app's Basic Information page."),
    )
    client_secret = forms.CharField(
        label=_("Slack Client Secret"),
        help_text=_("Copy from your Slack app's Basic Information page."),
    )
    signing_secret = forms.CharField(
        label=_("Slack Signing Secret"),
        help_text=_("Copy from your Slack app's Basic Information page."),
    )
    bot_token = forms.CharField(
        label=_("Slack Bot Token"),
        help_text=_("Copy the bot token (starts with xoxb-) from the Install App page."),
    )
    team_id = forms.CharField(
        label=_("Slack Team ID"),
        help_text=_("Optional. Specify a team if your Slack app is installed in multiple workspaces."),
        required=False,
    )
    scope = forms.CharField(
        label=_("Requested scopes"),
        help_text=_("Comma-separated scopes requested during Slack app installation."),
        required=False,
    )
    redirect_uri = forms.URLField(
        label=_("Redirect URI"),
        help_text=_("URL Slack redirects to after OAuth approval."),
        assume_scheme="https",
    )
