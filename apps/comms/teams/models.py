import json
import logging
from typing import Iterable

import contextlib
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityAllManager, EntityManager
from apps.sigils.fields import SigilShortAutoField
from apps.core.models import InviteLead as CoreInviteLead
from apps.users.models import Profile as CoreProfile
from apps.users.models import User as CoreUser
from apps.awg.models import PowerLead as CorePowerLead
from apps.nodes.models import Node


logger = logging.getLogger(__name__)


class SlackApiError(RuntimeError):
    """Raised when Slack reports an error during API calls."""


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


class SlackBotProfile(CoreProfile):
    """Store credentials required to operate a Slack chatbot."""

    API_BASE_URL = "https://slack.com/api"

    profile_fields = (
        "node",
        "team_id",
        "bot_user_id",
        "bot_token",
        "signing_secret",
        "default_channels",
        "is_enabled",
    )

    node = models.OneToOneField(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="slack_bot",
        null=True,
        blank=True,
        help_text=_("Node that owns this Slack chatbot."),
    )
    team_id = models.CharField(
        max_length=32,
        help_text=_("Slack workspace team identifier (starts with T)."),
    )
    bot_user_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Slack bot user identifier (starts with U or B)."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack bot token used for authenticated API calls."),
    )
    signing_secret = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack signing secret used to verify incoming requests."),
    )
    default_channels = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Channel identifiers where Net Messages should be posted. Provide"
            " a JSON array of channel IDs (for example [\"C01ABCDE\"])."
        ),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop the bot from posting to Slack."),
    )

    class Meta:
        verbose_name = _("Slack Chatbot")
        verbose_name_plural = _("Slack Chatbots")
        constraints = [
            models.UniqueConstraint(
                fields=["team_id"],
                name="slackbotprofile_team_id_unique",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                    | (Q(user__isnull=True) & Q(group__isnull=True) & Q(node__isnull=False))
                ),
                name="slackbotprofile_requires_owner",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        identifier = (self.resolve_sigils("team_id") or self.team_id or "").strip()
        owner = self.owner_display()
        if identifier and owner:
            return f"{identifier} ({owner})"
        if identifier:
            return identifier
        return owner or super().__str__()

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(CoreProfile, self).clean()

        errors = {}

        if not self.node_id and not self.user_id and not self.group_id:
            errors["node"] = _("Assign the Slack bot to a node or owner.")

        team_id = (self.team_id or "").strip().upper()
        if not team_id:
            errors["team_id"] = _("Provide the Slack workspace team identifier.")
        elif not team_id.startswith("T"):
            errors["team_id"] = _("Slack team identifiers start with the letter T.")

        if not (self.resolve_sigils("bot_token") or (self.bot_token or "").strip()):
            errors["bot_token"] = _(
                "Provide the Slack bot token so Arthexis can send messages."
            )

        if not (self.resolve_sigils("signing_secret") or (self.signing_secret or "").strip()):
            errors["signing_secret"] = _(
                "Provide the Slack signing secret so incoming requests can be verified."
            )

        channels_error = self._normalize_channels()
        if channels_error:
            errors["default_channels"] = channels_error

        if errors:
            raise ValidationError(errors)

        self.team_id = team_id

    def save(self, *args, **kwargs):
        if self.team_id:
            self.team_id = self.team_id.strip().upper()
        if self.bot_user_id:
            self.bot_user_id = self.bot_user_id.strip().upper()
        self._normalize_channels()
        super().save(*args, **kwargs)

    def owner_display(self):  # pragma: no cover - simple representation helper
        owner = super().owner_display()
        if owner:
            return owner
        if self.node_id and self.node:
            return str(self.node)
        return ""

    def _normalize_channels(self) -> str | None:
        """Ensure ``default_channels`` stores a list of strings."""

        channels = self.default_channels or []
        if isinstance(channels, str):
            try:
                channels = json.loads(channels)
            except json.JSONDecodeError:
                return _("Channel IDs must be provided as a JSON array of strings.")
        if channels is None:
            channels = []
        if not isinstance(channels, list):
            return _("Channel IDs must be provided as a list of strings.")
        normalized: list[str] = []
        for value in channels:
            if value is None:
                continue
            if not isinstance(value, str):
                return _("Channel IDs must be provided as a list of strings.")
            cleaned = value.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        self.default_channels = normalized
        return None

    # Public helpers -------------------------------------------------

    def get_bot_token(self) -> str:
        return (self.resolve_sigils("bot_token") or self.bot_token or "").strip()

    def get_signing_secret(self) -> str:
        return (self.resolve_sigils("signing_secret") or self.signing_secret or "").strip()

    def get_channels(self) -> list[str]:
        return list(self.default_channels or [])

    def connect(self) -> dict[str, object]:
        """Validate the stored credentials by calling Slack's ``auth.test``."""

        data = self._api_post("auth.test", {})
        team_id = (data.get("team_id") or "").strip().upper()
        bot_user_id = (data.get("user_id") or "").strip().upper()
        updated_fields: list[str] = []
        if team_id and not self.team_id:
            self.team_id = team_id
            updated_fields.append("team_id")
        if bot_user_id and not self.bot_user_id:
            self.bot_user_id = bot_user_id
            updated_fields.append("bot_user_id")
        if updated_fields:
            self.save(update_fields=updated_fields)
        return data

    def broadcast_net_message(self, message: "NetMessage") -> None:
        """Post ``message`` to each configured Slack channel."""

        if not self.is_enabled:
            return
        token = self.get_bot_token()
        if not token:
            logger.debug("Slack bot %s skipped broadcast: missing token", self.pk)
            return
        channels = self.get_channels()
        if not channels:
            logger.debug(
                "Slack bot %s skipped broadcast: no default channels configured",
                self.pk,
            )
            return

        subject = (message.subject or "").strip()
        body = (message.body or "").strip()
        if subject and body:
            text = f"*{subject}*\n{body}"
        else:
            text = subject or body

        attachments = []
        for attachment in message.attachments or []:
            if not isinstance(attachment, dict):
                continue
            descriptor = attachment.get("description")
            if isinstance(descriptor, str) and descriptor.strip():
                attachments.append(descriptor.strip())
                continue
            try:
                attachments.append(json.dumps(attachment, ensure_ascii=False))
            except (TypeError, ValueError):
                continue

        for channel in channels:
            payload: dict[str, object] = {"channel": channel, "text": text or ""}
            if attachments:
                attachment_text = "\n".join(attachments)
                payload["text"] = (payload["text"] or "").strip()
                if payload["text"]:
                    payload["text"] += "\n" + attachment_text
                else:
                    payload["text"] = attachment_text
            try:
                self._api_post("chat.postMessage", payload)
            except SlackApiError:
                logger.exception(
                    "Slack bot %s failed to post NetMessage %s to channel %s",
                    self.pk,
                    getattr(message, "pk", None),
                    channel,
                )

    # Internal utilities --------------------------------------------

    def _api_post(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        token = self.get_bot_token()
        if not token:
            raise SlackApiError("missing_token")
        url = f"{self.API_BASE_URL}/{method}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        response = None
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
        except requests.RequestException as exc:  # pragma: no cover - network issues
            raise SlackApiError("request_failed") from exc
        try:
            data: dict[str, object] | None
            try:
                data = response.json()
            except ValueError:
                data = None
            if not response.ok or not isinstance(data, dict) or not data.get("ok"):
                error = "unknown_error"
                if isinstance(data, dict):
                    error = str(data.get("error") or error)
                raise SlackApiError(error)
            return data
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    def _profile_fields(self) -> Iterable[str]:  # pragma: no cover - admin helper
        return self.profile_fields


    def _profile_fields(self) -> Iterable[str]:  # pragma: no cover - admin helper
        return self.profile_fields

