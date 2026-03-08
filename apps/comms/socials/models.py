from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile


social_domain_validator = RegexValidator(
    regex=r"^(?=.{1,253}\Z)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
    message=_("Enter a valid domain name such as example.com."),
    code="invalid",
)


social_did_validator = RegexValidator(
    regex=r"^(|did:[a-z0-9]+:[A-Za-z0-9.\-_:]+)$",
    message=_("Enter a valid DID such as did:plc:1234abcd."),
    code="invalid",
)


class AvatarProfile(Profile):
    """Base profile limited to chat avatars."""

    avatar = models.OneToOneField(
        "chats.ChatAvatar",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s",
        help_text=_("Chat avatar that owns this profile."),
    )

    class Meta(Profile.Meta):
        abstract = True

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        if self.user_id or self.group_id:
            errors["avatar"] = _("Social profiles must be attached to chat avatars.")
        if not self.avatar_id:
            errors["avatar"] = _("Assign the profile to a chat avatar.")
        if errors:
            raise ValidationError(errors)

    def owner_display(self) -> str:
        if self.avatar_id and self.avatar:
            name = self.avatar.owner_display()
            if name:
                return name
        return super().owner_display()


class BlueskyProfile(AvatarProfile):
    """Store configuration required to link Bluesky accounts."""

    profile_fields = (
        "handle",
        "domain",
        "did",
    )

    handle = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Bluesky handle that should resolve to Arthexis. Use the verified domain (for example arthexis.com)."
        ),
        validators=[social_domain_validator],
    )
    domain = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Domain that hosts the Bluesky verification. Publish a _atproto TXT record or a /.well-known/atproto-did file with the DID below."
        ),
        validators=[social_domain_validator],
    )
    did = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional DID that Bluesky assigns once the domain is linked (for example did:plc:1234abcd)."
        ),
        validators=[social_did_validator],
    )

    class Meta(AvatarProfile.Meta):
        verbose_name = _("Bluesky Identity")
        verbose_name_plural = _("Bluesky Identities")
        constraints = [
            models.UniqueConstraint(
                fields=["avatar"],
                name="blueskyprofile_avatar_unique",
            ),
            models.UniqueConstraint(
                fields=["handle"],
                condition=~models.Q(handle=""),
                name="blueskyprofile_handle_unique",
            ),
            models.UniqueConstraint(
                fields=["domain"],
                condition=~models.Q(domain=""),
                name="blueskyprofile_domain_unique",
            ),
        ]

    def clean(self):
        super().clean()
        self.handle = (self.handle or "").strip().lower()
        self.domain = (self.domain or "").strip().lower()
        errors = {}
        if not self.handle:
            errors["handle"] = _("Please provide the Bluesky handle to verify.")
        if not self.domain:
            errors["domain"] = _("Please provide the Bluesky domain to verify.")
        if errors:
            raise ValidationError(errors)

    # Public helpers -------------------------------------------------
    def get_handle(self) -> str:
        return (self.resolve_sigils("handle") or self.handle or "").strip().lower()

    def get_domain(self) -> str:
        return (self.resolve_sigils("domain") or self.domain or "").strip().lower()

    def get_did(self) -> str:
        return (self.resolve_sigils("did") or self.did or "").strip()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        handle = self.get_handle()
        domain = self.get_domain()
        if handle:
            return f"{handle}@bluesky"
        if domain:
            return f"{domain}@bluesky"
        owner = self.owner_display()
        return owner or super().__str__()


class DiscordProfile(AvatarProfile):
    """Store configuration required to link Discord bots."""

    profile_fields = (
        "application_id",
        "public_key",
        "guild_id",
        "bot_token",
        "default_channel_id",
    )

    application_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord application ID used to control the bot."),
    )
    public_key = models.CharField(
        max_length=128,
        blank=True,
        help_text=_("Discord public key used to verify interaction requests."),
    )
    guild_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord guild (server) identifier where the bot should operate."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_("Discord bot token required for authenticated actions."),
    )
    default_channel_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Optional Discord channel identifier used for default messaging."),
    )

    class Meta(AvatarProfile.Meta):
        verbose_name = _("Discord Identity")
        verbose_name_plural = _("Discord Identities")
        constraints = [
            models.UniqueConstraint(
                fields=["avatar"],
                name="discordprofile_avatar_unique",
            ),
            models.UniqueConstraint(
                fields=["guild_id"],
                condition=~models.Q(guild_id=""),
                name="discordprofile_guild_id_unique",
            ),
            models.UniqueConstraint(
                fields=["application_id"],
                condition=~models.Q(application_id=""),
                name="discordprofile_application_id_unique",
            ),
        ]

    def clean(self):
        super().clean()
        for field_name in (
            "application_id",
            "guild_id",
            "public_key",
            "bot_token",
            "default_channel_id",
        ):
            value = getattr(self, field_name, "")
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed != value:
                    setattr(self, field_name, trimmed)
        errors = {}
        for required in ("application_id", "guild_id", "bot_token"):
            if not getattr(self, required):
                errors[required] = _("This field is required for Discord profiles.")
        if errors:
            raise ValidationError(errors)

    # Public helpers -------------------------------------------------
    def get_application_id(self) -> str:
        return (self.resolve_sigils("application_id") or self.application_id or "").strip()

    def get_public_key(self) -> str:
        return (self.resolve_sigils("public_key") or self.public_key or "").strip()

    def get_guild_id(self) -> str:
        return (self.resolve_sigils("guild_id") or self.guild_id or "").strip()

    def get_bot_token(self) -> str:
        return (self.resolve_sigils("bot_token") or self.bot_token or "").strip()

    def get_default_channel_id(self) -> str:
        return (self.resolve_sigils("default_channel_id") or self.default_channel_id or "").strip()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        guild_id = self.get_guild_id()
        if guild_id:
            return f"{guild_id}@discord"
        owner = self.owner_display()
        return owner or super().__str__()
