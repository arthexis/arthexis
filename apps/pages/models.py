from __future__ import annotations

import base64
import contextlib
import logging
import uuid
from datetime import timedelta
from pathlib import Path

import json
import requests

from django.db import models
from django.db.models import Q
from django.core.validators import RegexValidator
from apps.core.entity import Entity, EntityManager
from apps.core.models import Lead, SecurityGroup
from apps.crms.models import OdooProfile
from django.contrib.sites.models import Site
from apps.nodes.models import ContentSample, NodeRole
from django.utils import timezone
from django.utils.text import slugify
from django.utils.html import conditional_escape, format_html, linebreaks
from django.utils.translation import gettext, gettext_lazy as _, get_language_info
from importlib import import_module
from django.urls import URLPattern, reverse
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError

from apps.app.models import Application
from apps.repos import github_issues
from .tasks import create_user_story_github_issue
from .site_config import ensure_site_fields


ensure_site_fields()


logger = logging.getLogger(__name__)


_HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}){1,2}$",
    message="Enter a valid hex color code (e.g. #0d6efd).",
)


class ModuleManager(models.Manager):
    def get_by_natural_key(self, role: str, path: str):
        return self.get(node_role__name=role, path=path)


class Module(Entity):
    node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    path = models.CharField(
        max_length=100,
        help_text="Base path for the app, starting with /",
        blank=True,
    )
    menu = models.CharField(
        max_length=100,
        blank=True,
        help_text="Text used for the navbar pill; defaults to the application name.",
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Lower values appear first in navigation pills.",
    )
    is_default = models.BooleanField(default=False)
    favicon = models.ImageField(upload_to="modules/favicons/", blank=True)

    objects = ModuleManager()

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        unique_together = ("node_role", "path")

    def natural_key(self):  # pragma: no cover - simple representation
        role_name = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        return (role_name, self.path)

    natural_key.dependencies = ["nodes.NodeRole"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    @property
    def menu_label(self) -> str:
        return self.menu or self.application.name

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
        super().save(*args, **kwargs)

    def create_landings(self):
        try:
            urlconf = import_module(f"{self.application.name}.urls")
        except Exception:
            try:
                urlconf = import_module(f"{self.application.name.lower()}.urls")
            except Exception:
                Landing.objects.get_or_create(
                    module=self,
                    path=self.path,
                    defaults={"label": self.application.name},
                )
                return
        patterns = getattr(urlconf, "urlpatterns", [])
        created = False
        normalized_module = self.path.strip("/")

        def _walk(patterns, prefix=""):
            nonlocal created
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    callback = pattern.callback
                    if getattr(callback, "landing", False):
                        pattern_path = str(pattern.pattern)
                        relative = f"{prefix}{pattern_path}"
                        if normalized_module and relative.startswith(normalized_module):
                            full_path = f"/{relative}"
                            Landing.objects.update_or_create(
                                module=self,
                                path=full_path,
                                defaults={
                                    "label": getattr(
                                        callback,
                                        "landing_label",
                                        callback.__name__.replace("_", " ").title(),
                                    )
                                },
                            )
                        else:
                            full_path = f"{self.path}{relative}"
                            Landing.objects.get_or_create(
                                module=self,
                                path=full_path,
                                defaults={
                                    "label": getattr(
                                        callback,
                                        "landing_label",
                                        callback.__name__.replace("_", " ").title(),
                                    )
                                },
                            )
                        created = True
                else:
                    _walk(
                        pattern.url_patterns, prefix=f"{prefix}{str(pattern.pattern)}"
                    )

        _walk(patterns)

        if not created:
            Landing.objects.get_or_create(
                module=self, path=self.path, defaults={"label": self.application.name}
            )


class SiteTemplateManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class SiteTemplate(Entity):
    name = models.CharField(max_length=100, unique=True)
    primary_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    primary_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    accent_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    accent_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    support_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    support_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    support_text_color = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )

    objects = SiteTemplateManager()

    class Meta:
        verbose_name = _("Site Template")
        verbose_name_plural = _("Site Templates")
        ordering = ("name",)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @staticmethod
    def _hex_to_rgb(value: str) -> str:
        cleaned = value.lstrip("#")
        if len(cleaned) == 3:
            cleaned = "".join(ch * 2 for ch in cleaned)
        if len(cleaned) != 6:
            return ""
        try:
            r = int(cleaned[0:2], 16)
            g = int(cleaned[2:4], 16)
            b = int(cleaned[4:6], 16)
        except ValueError:
            return ""
        return f"{r}, {g}, {b}"

    @property
    def primary_rgb(self) -> str:
        return self._hex_to_rgb(self.primary_color)

    @property
    def accent_rgb(self) -> str:
        return self._hex_to_rgb(self.accent_color)

    @property
    def support_rgb(self) -> str:
        return self._hex_to_rgb(self.support_color)


class SiteBadge(Entity):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    favicon = models.ImageField(upload_to="sites/favicons/", blank=True)
    landing_override = models.ForeignKey(
        "Landing", null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"

    class Meta:
        verbose_name = "Site Badge"
        verbose_name_plural = "Site Badges"


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = "Site"
        verbose_name_plural = "Sites"
        default_permissions = ()
        permissions = [
            ("add_siteproxy", "Can add site"),
            ("change_siteproxy", "Can change site"),
            ("delete_siteproxy", "Can delete site"),
            ("view_siteproxy", "Can view site"),
        ]


class DeveloperArticleManager(EntityManager):
    """Manager providing helpers for developer-authored articles."""

    def get_by_natural_key(self, slug: str):
        return self.get(slug=slug)

    def published(self):
        """Return only the articles that are published."""

        return super().get_queryset().filter(is_published=True)


class DeveloperArticle(Entity):
    """Editorial content authored by developers for public consumption."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    summary = models.TextField(blank=True, default="")
    content = models.TextField()
    is_published = models.BooleanField(default=False)
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    objects = DeveloperArticleManager()

    class Meta:
        ordering = ("-created_on", "title")
        verbose_name = _("Developer Article")
        verbose_name_plural = _("Developer Articles")

    def __str__(self) -> str:  # pragma: no cover - human readable
        return self.title

    def natural_key(self):  # pragma: no cover - natural reference
        return (self.slug,)

    def get_absolute_url(self) -> str:
        return reverse("pages:developer-article", kwargs={"slug": self.slug})

    def clean(self):
        super().clean()
        if not self.slug:
            raise ValidationError({"slug": _("Slug is required for developer articles.")})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        self.full_clean()
        super().save(*args, **kwargs)


class OdooChatBridgeManager(EntityManager):
    """Manager providing helpers for Odoo chat bridges."""

    def for_site(self, site: Site | None):
        queryset = self.filter(is_enabled=True)
        if site and getattr(site, "pk", None):
            bridge = queryset.filter(site=site).first()
            if bridge:
                return bridge
        return queryset.filter(is_default=True).first()


class WhatsAppChatBridgeManager(EntityManager):
    """Manager providing helpers for WhatsApp chat bridges."""

    def for_site(self, site: Site | None):
        queryset = self.filter(is_enabled=True)
        if site and getattr(site, "pk", None):
            bridge = queryset.filter(site=site).first()
            if bridge:
                return bridge
        return queryset.filter(is_default=True).first()


class OdooChatBridge(Entity):
    """Configuration for forwarding visitor chat messages to Odoo."""

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="odoo_chat_bridges",
        null=True,
        blank=True,
        help_text=_("Restrict this bridge to a specific site. Leave blank to use it as a fallback."),
    )
    profile = models.ForeignKey(
        OdooProfile,
        on_delete=models.CASCADE,
        related_name="chat_bridges",
        help_text=_("Verified Odoo employee credentials used to post chat messages."),
    )
    channel_id = models.PositiveIntegerField(
        help_text=_("Identifier of the Odoo mail.channel that should receive forwarded messages."),
        verbose_name=_("Channel ID"),
    )
    channel_uuid = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Optional UUID of the Odoo mail.channel for reference."),
        verbose_name=_("Channel UUID"),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop forwarding chat messages to this Odoo channel."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("Use as the fallback bridge when no site-specific configuration is defined."),
    )
    notify_partner_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Additional Odoo partner IDs to notify when posting messages. Provide a JSON array of integers."),
    )

    objects = OdooChatBridgeManager()

    class Meta:
        ordering = ["site__domain", "pk"]
        verbose_name = _("Odoo Chat Bridge")
        verbose_name_plural = _("Odoo Chat Bridges")
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=Q(site__isnull=False),
                name="unique_odoo_chat_bridge_site",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="single_default_odoo_chat_bridge",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.site_id and self.site:
            return _("%(site)s → Odoo channel %(channel)s") % {
                "site": self.site,
                "channel": self.channel_id,
            }
        if self.is_default:
            return _("Default Odoo chat bridge (%(channel)s)") % {"channel": self.channel_id}
        return str(self.channel_id)

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if self.channel_id and self.channel_id <= 0:
            errors.setdefault("channel_id", []).append(
                _("Provide the numeric identifier of the Odoo mail channel."),
            )
        try:
            normalized = self._normalize_partner_ids(self.notify_partner_ids)
        except ValidationError as exc:
            raise exc
        else:
            self.notify_partner_ids = normalized
        if self.is_default and self.site_id:
            errors.setdefault("is_default", []).append(
                _("Default Odoo chat bridges cannot target a specific site."),
            )
        if errors:
            raise ValidationError(errors)

    def partner_ids(self) -> list[int]:
        """Return the Odoo partner IDs that should be notified."""

        partner_ids: list[int] = []
        profile_partner = getattr(self.profile, "partner_id", None)
        if profile_partner:
            try:
                parsed = int(profile_partner)
            except (TypeError, ValueError):
                parsed = None
            else:
                if parsed > 0:
                    partner_ids.append(parsed)
        for ident in self.notify_partner_ids or []:
            try:
                parsed = int(ident)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in partner_ids:
                partner_ids.append(parsed)
        return partner_ids

    def post_message(self, session: "ChatSession", message: "ChatMessage") -> bool:
        """Relay ``message`` to the configured Odoo channel."""

        if not self.is_enabled:
            return False
        if not self.profile or not self.profile.is_verified:
            return False
        content = (message.body or "").strip()
        if not content:
            return False
        subject = gettext("Visitor chat %(uuid)s") % {"uuid": session.uuid}
        body = self._render_body(session, message, content)
        payload: dict[str, object] = {
            "body": body,
            "subject": subject,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_comment",
        }
        partners = self.partner_ids()
        if partners:
            payload["partner_ids"] = partners
        try:
            self.profile.execute(
                "mail.channel",
                "message_post",
                [self.channel_id],
                payload,
            )
        except Exception:
            logger.exception(
                "Failed to forward chat message %s for session %s to Odoo channel %s",
                getattr(message, "pk", None),
                getattr(session, "pk", None),
                self.channel_id,
            )
            return False
        return True

    def _render_body(self, session: "ChatSession", message: "ChatMessage", content: str) -> str:
        author = conditional_escape(message.author_label())
        body_content = linebreaks(content)
        metadata_parts: list[str] = []
        if session.site_id and session.site:
            metadata_parts.append(str(session.site))
        if session.pk:
            try:
                admin_path = reverse("admin:pages_chatsession_change", args=[session.pk])
            except Exception:
                admin_path = ""
            else:
                metadata_parts.append(gettext("Admin: %(path)s") % {"path": admin_path})
        metadata_parts.append(
            gettext("Author: %(label)s")
            % {"label": gettext("Staff") if message.from_staff else gettext("Visitor")}
        )
        timestamp = getattr(message, "created_at", None)
        if timestamp:
            try:
                display_ts = timezone.localtime(timestamp)
            except (TypeError, ValueError, AttributeError):
                display_ts = timestamp
            metadata_parts.append(display_ts.strftime("%Y-%m-%d %H:%M:%S %Z").strip())
        metadata_parts.append(str(session.uuid))
        meta_text = " • ".join(part for part in metadata_parts if part)
        return format_html(
            "<p><strong>{author}</strong></p>{content}<p><small>{meta}</small></p>",
            author=author,
            content=body_content,
            meta=meta_text,
        )

    def _normalize_partner_ids(self, values: object) -> list[int]:
        if not values:
            return []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be provided as a JSON array of integers.")}
                ) from exc
        if not isinstance(values, list):
            raise ValidationError(
                {"notify_partner_ids": _("Partner IDs must be provided as a list of integers.")}
            )
        normalized: list[int] = []
        for item in values:
            if item in (None, ""):
                continue
            try:
                ident = int(item)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be integers.")}
                ) from exc
            if ident <= 0:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be positive integers.")}
                )
            if ident not in normalized:
                normalized.append(ident)
        return normalized


class WhatsAppChatBridge(Entity):
    """Configuration for forwarding chat messages to WhatsApp."""

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="whatsapp_chat_bridges",
        null=True,
        blank=True,
        help_text=_("Restrict this bridge to a specific site. Leave blank to use it as a fallback."),
    )
    api_base_url = models.URLField(
        default="https://graph.facebook.com/v18.0",
        help_text=_("Base URL for the WhatsApp Cloud API."),
        verbose_name=_("API Base URL"),
    )
    phone_number_id = models.CharField(
        max_length=100,
        help_text=_("Identifier of the WhatsApp phone number used to send messages."),
        verbose_name=_("Phone Number ID"),
    )
    access_token = models.CharField(
        max_length=255,
        help_text=_("Bearer token used to authenticate against the WhatsApp API."),
        verbose_name=_("Access Token"),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop forwarding chat messages through this WhatsApp client."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("Use as the fallback bridge when no site-specific configuration is defined."),
    )

    objects = WhatsAppChatBridgeManager()

    class Meta:
        ordering = ["site__domain", "pk"]
        verbose_name = _("WhatsApp Chat Bridge")
        verbose_name_plural = _("WhatsApp Chat Bridges")
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=Q(site__isnull=False),
                name="unique_whatsapp_chat_bridge_site",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="single_default_whatsapp_chat_bridge",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.site_id and self.site:
            return _("%(site)s → WhatsApp phone %(phone)s") % {
                "site": self.site,
                "phone": self.phone_number_id,
            }
        if self.is_default:
            return _("Default WhatsApp chat bridge (%(phone)s)") % {
                "phone": self.phone_number_id
            }
        return str(self.phone_number_id)

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.phone_number_id:
            errors.setdefault("phone_number_id", []).append(
                _("Provide the WhatsApp phone number identifier used for delivery."),
            )
        if not self.access_token:
            errors.setdefault("access_token", []).append(
                _("Access token is required to authenticate with WhatsApp."),
            )
        if self.is_default and self.site_id:
            errors.setdefault("is_default", []).append(
                _("Default WhatsApp chat bridges cannot target a specific site."),
            )
        if errors:
            raise ValidationError(errors)

    def send_message(
        self,
        *,
        recipient: str,
        content: str,
        session: "ChatSession" | None = None,
        message: "ChatMessage" | None = None,
    ) -> bool:
        """Send ``content`` to ``recipient`` via WhatsApp."""

        if not self.is_enabled:
            return False
        recipient = (recipient or "").strip()
        token = (self.access_token or "").strip()
        if not recipient or not token:
            return False
        content = (content or "").strip()
        if not content:
            return False
        endpoint = f"{self.api_base_url.rstrip('/')}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": content[:4096]},
        }
        timeout = getattr(settings, "PAGES_WHATSAPP_TIMEOUT", 10)
        response = None
        try:
            response = requests.post(
                endpoint, json=payload, headers=headers, timeout=timeout
            )
        except Exception:
            logger.exception(
                "Failed to send WhatsApp message %s for session %s",
                getattr(message, "pk", None),
                getattr(session, "pk", None),
            )
            return False
        try:
            if response.status_code >= 400:
                logger.warning(
                    "WhatsApp API returned %s for session %s: %s",
                    response.status_code,
                    getattr(session, "pk", None),
                    getattr(response, "text", ""),
                )
                return False
            return True
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()


from . import odoo as odoo_bridge
from . import whatsapp as whatsapp_bridge


class ChatSession(Entity):
    """Persistent conversation between a visitor and staff."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ESCALATED = "escalated", _("Escalated")
        CLOSED = "closed", _("Closed")

    uuid = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, verbose_name=_("UUID")
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        related_name="chat_sessions",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="chat_sessions",
        null=True,
        blank=True,
    )
    visitor_key = models.CharField(max_length=64, blank=True, db_index=True)
    whatsapp_number = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("WhatsApp sender identifier associated with the chat session."),
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    last_visitor_activity_at = models.DateTimeField(null=True, blank=True)
    last_staff_activity_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_activity_at", "-pk"]

    def save(self, *args, **kwargs):
        self.is_user_data = True
        if not self.last_activity_at:
            self.last_activity_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Chat session {self.uuid}"

    def assign_user(self, user) -> None:
        if not user or not getattr(user, "is_authenticated", False):
            return
        if self.user_id == user.id:
            return
        self.user = user
        self.save(update_fields=["user"])

    def touch_activity(self, *, visitor: bool = False, staff: bool = False) -> None:
        now = timezone.now()
        updates: dict[str, object] = {"last_activity_at": now}
        if visitor:
            updates["last_visitor_activity_at"] = now
        if staff:
            updates["last_staff_activity_at"] = now
        if self.status == self.Status.CLOSED:
            updates["status"] = self.Status.OPEN
            updates["closed_at"] = None
        elif staff:
            updates["status"] = self.Status.OPEN
        for field, value in updates.items():
            setattr(self, field, value)
        update_fields = list(updates.keys())
        self.save(update_fields=update_fields)

    def close(self) -> None:
        if self.status == self.Status.CLOSED:
            return
        now = timezone.now()
        self.status = self.Status.CLOSED
        self.closed_at = now
        self.save(update_fields=["status", "closed_at"])

    def can_join(self, visitor_key: str | None, user) -> bool:
        if user and getattr(user, "is_staff", False):
            return True
        if user and getattr(user, "is_authenticated", False) and self.user_id == user.id:
            return True
        expected = (self.visitor_key or "").strip()
        provided = (visitor_key or "").strip()
        return bool(expected) and expected == provided

    def add_message(
        self,
        *,
        content: str,
        sender=None,
        from_staff: bool = False,
        display_name: str = "",
        source: str = "",
    ):
        message = ChatMessage(
            session=self,
            sender=sender if getattr(sender, "is_authenticated", False) else None,
            sender_display_name=display_name[:150],
            from_staff=from_staff,
            body=content,
        )
        message.is_user_data = True
        message.save()
        if (source or "").lower() != "whatsapp":
            whatsapp_bridge.forward_chat_message(self, message)
        odoo_bridge.forward_chat_message(self, message)
        self.touch_activity(visitor=not from_staff, staff=from_staff)
        if not from_staff:
            self.notify_staff_of_message(message)
            self.maybe_escalate_on_idle()
        return message

    def maybe_escalate_on_idle(self) -> bool:
        notify = getattr(settings, "PAGES_CHAT_NOTIFY_STAFF", False)
        if not notify:
            return False
        idle_seconds = getattr(settings, "PAGES_CHAT_IDLE_ESCALATE_SECONDS", 0)
        try:
            idle_seconds = int(idle_seconds)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            idle_seconds = 0
        now = timezone.now()
        if idle_seconds <= 0:
            threshold = now
        else:
            threshold = now - timedelta(seconds=idle_seconds)
        last_staff = self.last_staff_activity_at or self.created_at
        if last_staff and last_staff >= threshold:
            return False
        if self.escalated_at and (
            last_staff is None or self.escalated_at >= last_staff
        ):
            return False
        self.escalated_at = now
        self.status = self.Status.ESCALATED
        try:
            self.save(update_fields=["escalated_at", "status"])
        except Exception:  # pragma: no cover - database failures logged
            logger.exception("Failed to record escalation for chat session %s", self.pk)
            return False
        subject = gettext("Visitor chat awaiting staff response")
        body = gettext("Chat session %(uuid)s is idle and needs staff attention.") % {
            "uuid": self.uuid
        }
        try:
            from apps.nodes.models import NetMessage

            NetMessage.broadcast(subject=subject, body=body)
        except Exception:  # pragma: no cover - propagation errors handled in logs
            logger.exception(
                "Failed to broadcast escalation NetMessage for chat session %s",
                self.pk,
            )
        return True

    def notify_staff_of_message(self, message: "ChatMessage") -> bool:
        notify = getattr(settings, "PAGES_CHAT_NOTIFY_STAFF", False)
        if not notify:
            return False

        subject = gettext("New visitor chat message")
        snippet = (message.body or "").strip()
        if snippet:
            snippet = " ".join(snippet.split())
        author = message.author_label()
        if snippet and author:
            snippet_text = gettext("%(author)s: %(snippet)s") % {
                "author": author,
                "snippet": snippet,
            }
        elif snippet:
            snippet_text = snippet
        else:
            snippet_text = gettext("Visitor sent a message without additional text.")

        if len(snippet_text) > 180:
            snippet_text = f"{snippet_text[:177]}..."

        admin_path = ""
        try:
            admin_path = reverse("admin:pages_chatsession_change", args=[self.pk])
        except Exception:
            logger.exception(
                "Failed to resolve admin URL for chat session %s", self.pk
            )

        body_parts = [
            gettext("Chat session %(uuid)s received a new visitor message.")
            % {"uuid": self.uuid}
        ]
        if snippet_text:
            body_parts.append(
                gettext("Latest message: %(snippet)s") % {"snippet": snippet_text}
            )
        if admin_path:
            body_parts.append(
                gettext("Review conversation in admin: %(path)s")
                % {"path": admin_path}
            )

        body = "\n\n".join(part for part in body_parts if part).strip()
        body = body[:256]

        try:
            from apps.nodes.models import NetMessage

            NetMessage.broadcast(subject=subject, body=body)
        except Exception:
            logger.exception(
                "Failed to broadcast chat notification for session %s", self.pk
            )
            return False
        return True


class ChatMessage(Entity):
    """Individual message stored for a chat session."""

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        null=True,
        blank=True,
    )
    sender_display_name = models.CharField(max_length=150, blank=True)
    from_staff = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args, **kwargs):
        self.is_user_data = True
        super().save(*args, **kwargs)

    def author_label(self) -> str:
        if self.sender_display_name:
            return self.sender_display_name
        if self.sender_id and self.sender:
            return getattr(self.sender, "get_full_name", lambda: "")() or getattr(
                self.sender, "username", ""
            )
        return gettext("Visitor") if not self.from_staff else gettext("Staff")

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.pk,
            "content": self.body,
            "created": self.created_at.isoformat(),
            "from_staff": self.from_staff,
            "author": self.author_label(),
        }


class LandingManager(models.Manager):
    def get_by_natural_key(self, role: str, module_path: str, path: str):
        return self.get(
            module__node_role__name=role, module__path=module_path, path=path
        )


class Landing(Entity):
    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="landings"
    )
    path = models.CharField(max_length=200)
    label = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    track_leads = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    objects = LandingManager()

    class Meta:
        unique_together = ("module", "path")
        verbose_name = _("Landing")
        verbose_name_plural = _("Landings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.label} ({self.path})"

    def save(self, *args, **kwargs):
        existing = None
        if not self.pk:
            existing = (
                type(self).objects.filter(module=self.module, path=self.path).first()
            )
        if existing:
            self.pk = existing.pk
        super().save(*args, **kwargs)


class LandingLead(Lead):
    landing = models.ForeignKey(
        "pages.Landing", on_delete=models.CASCADE, related_name="leads"
    )

    class Meta:
        verbose_name = _("Landing Lead")
        verbose_name_plural = _("Landing Leads")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.landing.label} ({self.path})"


class RoleLandingManager(models.Manager):
    def get_by_natural_key(
        self,
        role: str | None,
        group: str | None,
        username: str | None,
        module_path: str,
        path: str,
    ):
        filters = {
            "landing__module__path": module_path,
            "landing__path": path,
        }
        if role:
            filters["node_role__name"] = role
        else:
            filters["node_role__isnull"] = True
        if group:
            filters["security_group__name"] = group
        else:
            filters["security_group__isnull"] = True
        if username:
            filters["user__username"] = username
        else:
            filters["user__isnull"] = True
        return self.get(**filters)


class RoleLanding(Entity):
    node_role = models.OneToOneField(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    security_group = models.OneToOneField(
        SecurityGroup,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    landing = models.ForeignKey(
        Landing,
        on_delete=models.CASCADE,
        related_name="role_defaults",
    )
    priority = models.IntegerField(default=0)

    objects = RoleLandingManager()

    class Meta:
        verbose_name = _("Default Landing")
        verbose_name_plural = _("Default Landings")
        ordering = ("-priority", "pk")
        constraints = [
            models.CheckConstraint(
                name="pages_rolelanding_single_target",
                condition=(
                    Q(
                        node_role__isnull=False,
                        security_group__isnull=True,
                        user__isnull=True,
                    )
                    | Q(
                        node_role__isnull=True,
                        security_group__isnull=False,
                        user__isnull=True,
                    )
                    | Q(
                        node_role__isnull=True,
                        security_group__isnull=True,
                        user__isnull=False,
                    )
                ),
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.node_role_id:
            role_name = self.node_role.name
        elif self.security_group_id:
            role_name = self.security_group.name
        elif self.user_id:
            role_name = self.user.get_username()
        else:  # pragma: no cover - guarded by constraint
            role_name = "?"
        landing_path = self.landing.path if self.landing_id else "?"
        return f"{role_name} → {landing_path}"

    def natural_key(self):  # pragma: no cover - simple representation
        role_name = None
        group_name = None
        username = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        if getattr(self, "security_group_id", None):
            group_name = self.security_group.name
        if getattr(self, "user_id", None):
            username = self.user.get_username()
        landing_key = (None, None)
        if getattr(self, "landing_id", None):
            landing_key = (
                self.landing.module.path if self.landing.module_id else None,
                self.landing.path,
            )
        return (role_name, group_name, username) + landing_key

    natural_key.dependencies = [
        "nodes.NodeRole",
        "core.SecurityGroup",
        settings.AUTH_USER_MODEL,
        "pages.Landing",
    ]

    def clean(self):
        super().clean()
        targets = [
            bool(self.node_role_id),
            bool(self.security_group_id),
            bool(self.user_id),
        ]
        if sum(targets) == 0:
            raise ValidationError(
                {
                    "node_role": _("Select a node role, security group, or user."),
                    "security_group": _(
                        "Select a node role, security group, or user."
                    ),
                    "user": _("Select a node role, security group, or user."),
                }
            )
        if sum(targets) > 1:
            raise ValidationError(
                {
                    "node_role": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                    "security_group": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                    "user": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                }
            )

class UserManual(Entity):
    class PdfOrientation(models.TextChoices):
        LANDSCAPE = "landscape", _("Landscape")
        PORTRAIT = "portrait", _("Portrait")

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    languages = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Comma-separated 2-letter language codes",
    )
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")
    pdf_orientation = models.CharField(
        max_length=10,
        choices=PdfOrientation.choices,
        default=PdfOrientation.LANDSCAPE,
        help_text=_("Orientation used when rendering the PDF download."),
    )

    class Meta:
        db_table = "man_usermanual"
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"

    def __str__(self):  # pragma: no cover - simple representation
        return self.title

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.slug,)

    def _ensure_pdf_is_base64(self) -> None:
        """Normalize ``content_pdf`` so stored values are base64 strings."""

        value = self.content_pdf
        if value in {None, ""}:
            self.content_pdf = "" if value is None else value
            return

        if isinstance(value, (bytes, bytearray, memoryview)):
            self.content_pdf = base64.b64encode(bytes(value)).decode("ascii")
            return

        reader = getattr(value, "read", None)
        if callable(reader):
            data = reader()
            if hasattr(value, "seek"):
                try:
                    value.seek(0)
                except Exception:  # pragma: no cover - best effort reset
                    pass
            self.content_pdf = base64.b64encode(data).decode("ascii")
            return

        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("data:"):
                _, _, encoded = stripped.partition(",")
                self.content_pdf = encoded.strip()

    def save(self, *args, **kwargs):
        self._ensure_pdf_is_base64()
        super().save(*args, **kwargs)


class ViewHistory(Entity):
    """Record of public site visits."""

    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField()
    status_text = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    view_name = models.CharField(max_length=200, blank=True)
    visited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-visited_at"]
        verbose_name = _("View History")
        verbose_name_plural = _("View Histories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.method} {self.path} ({self.status_code})"

    @classmethod
    def purge_older_than(cls, *, days: int) -> int:
        """Delete history entries recorded more than ``days`` days ago."""

        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = cls.objects.filter(visited_at__lt=cutoff).delete()
        return deleted


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)

    class Meta:
        unique_together = ("user", "content_type")
        ordering = ["priority", "pk"]
        verbose_name = _("Favorite")
        verbose_name_plural = _("Favorites")


class UserStory(Lead):
    path = models.CharField(max_length=500)
    name = models.CharField(max_length=40, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Rate your experience from 1 (lowest) to 5 (highest)."),
    )
    comments = models.TextField(
        validators=[MaxLengthValidator(400)],
        help_text=_("Share more about your experience."),
    )
    take_screenshot = models.BooleanField(
        default=True,
        help_text=_("Request a screenshot capture for this feedback."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="user_stories",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="owned_user_stories",
        help_text=_("Internal owner for this feedback."),
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    github_issue_number = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=_("Number of the GitHub issue created for this feedback."),
    )
    github_issue_url = models.URLField(
        blank=True,
        help_text=_("Link to the GitHub issue created for this feedback."),
    )
    screenshot = models.ForeignKey(
        ContentSample,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="user_stories",
        help_text=_("Screenshot captured for this feedback."),
    )
    language_code = models.CharField(
        max_length=15,
        blank=True,
        help_text=_("Language selected when the feedback was submitted."),
    )

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = _("User Story")
        verbose_name_plural = _("User Stories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        display = self.name or _("Anonymous")
        return f"{display} ({self.rating}/5)"

    def get_github_issue_labels(self) -> list[str]:
        """Return default labels used when creating GitHub issues."""

        return ["feedback"]

    def get_github_issue_fingerprint(self) -> str | None:
        """Return a fingerprint used to avoid duplicate issue submissions."""

        if self.pk:
            return f"user-story:{self.pk}"
        return None

    def build_github_issue_title(self) -> str:
        """Return the title used for GitHub issues."""

        path = self.path or "/"
        return gettext("Feedback for %(path)s (%(rating)s/5)") % {
            "path": path,
            "rating": self.rating,
        }

    def build_github_issue_body(self) -> str:
        """Return the issue body summarising the feedback details."""

        name = self.name or gettext("Anonymous")
        path = self.path or "/"
        screenshot_requested = gettext("Yes") if self.take_screenshot else gettext("No")

        lines = [
            f"**Path:** {path}",
            f"**Rating:** {self.rating}/5",
            f"**Name:** {name}",
            f"**Screenshot requested:** {screenshot_requested}",
        ]

        language_code = (self.language_code or "").strip()
        if language_code:
            normalized = language_code.replace("_", "-").lower()
            try:
                info = get_language_info(normalized)
            except KeyError:
                language_display = ""
            else:
                language_display = info.get("name_local") or info.get("name") or ""

            if language_display:
                lines.append(f"**Language:** {language_display} ({normalized})")
            else:
                lines.append(f"**Language:** {normalized}")

        if self.submitted_at:
            lines.append(f"**Submitted at:** {self.submitted_at.isoformat()}")

        comment = (self.comments or "").strip()
        if comment:
            lines.extend(["", comment])

        return "\n".join(lines).strip()

    def create_github_issue(self) -> str | None:
        """Create a GitHub issue for this feedback and store the identifiers."""

        if self.github_issue_url:
            return self.github_issue_url

        response = github_issues.create_issue(
            self.build_github_issue_title(),
            self.build_github_issue_body(),
            labels=self.get_github_issue_labels(),
            fingerprint=self.get_github_issue_fingerprint(),
        )

        if response is None:
            return None

        try:
            try:
                payload = response.json()
            except ValueError:  # pragma: no cover - defensive guard
                payload = {}
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

        issue_url = payload.get("html_url")
        issue_number = payload.get("number")

        update_fields = []
        if issue_url and issue_url != self.github_issue_url:
            self.github_issue_url = issue_url
            update_fields.append("github_issue_url")
        if issue_number is not None and issue_number != self.github_issue_number:
            self.github_issue_number = issue_number
            update_fields.append("github_issue_number")

        if update_fields:
            self.save(update_fields=update_fields)

        return issue_url


from django.db.models.signals import post_save
from django.dispatch import receiver


def _celery_lock_path() -> Path:
    return Path(settings.BASE_DIR) / ".locks" / "celery.lck"


def _is_celery_enabled() -> bool:
    return _celery_lock_path().exists()


@receiver(post_save, sender=UserStory)
def _queue_low_rating_user_story_issue(
    sender, instance: UserStory, created: bool, raw: bool, **kwargs
) -> None:
    if raw or not created:
        return
    if instance.rating >= 5:
        return
    if instance.github_issue_url:
        return
    if not instance.user_id:
        return
    if not _is_celery_enabled():
        return

    try:
        create_user_story_github_issue.delay(instance.pk)
    except Exception:  # pragma: no cover - logging only
        logger.exception(
            "Failed to enqueue GitHub issue creation for user story %s", instance.pk
        )


@receiver(post_save, sender=Module)
def _create_landings(
    sender, instance, created, raw, **kwargs
):  # pragma: no cover - simple handler
    if created and not raw:
        instance.create_landings()
