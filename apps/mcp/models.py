from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.sigils.fields import SigilShortAutoField


def _generate_slug() -> str:
    return uuid.uuid4().hex


def _generate_secret() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class MCPServerEndpoints:
    base_path: str
    rpc_url: str
    events_url: str
    manifest_url: str


class MCPServer(Entity):
    name = models.CharField(
        max_length=150,
        unique=True,
        help_text=_("Human friendly label for this MCP server."),
    )
    slug = models.SlugField(
        max_length=64,
        unique=True,
        default=_generate_slug,
        help_text=_("Unique path component used to address this MCP server."),
    )
    acting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mcp_servers",
        help_text=_("User account the MCP service should act as when invoked."),
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text=_("Controls whether the MCP server is reachable."),
    )
    api_secret = SigilShortAutoField(
        max_length=255,
        default=_generate_secret,
        help_text=_(
            "Secret required by agents to retrieve MCP configuration and invoke endpoints."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("MCP server")
        verbose_name_plural = _("MCP servers")
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.name

    def has_valid_secret(self, candidate: str | None) -> bool:
        return bool(candidate) and secrets.compare_digest(
            self.api_secret or "", str(candidate)
        )

    def build_endpoints(self, request) -> MCPServerEndpoints:
        base_path = reverse("mcp:mcp_rpc", args=[self.slug])
        base_url = request.build_absolute_uri("/").rstrip("/")

        return MCPServerEndpoints(
            base_path=base_path,
            rpc_url=f"{base_url}{base_path}",
            events_url=request.build_absolute_uri(reverse("mcp:mcp_events", args=[self.slug])),
            manifest_url=request.build_absolute_uri(
                reverse("mcp_api:mcp_api_manifest", args=[self.slug])
            ),
        )

    def manifest(self, request) -> dict[str, str | dict[str, str]]:
        endpoints = self.build_endpoints(request)
        return {
            "name": self.name,
            "slug": self.slug,
            "acting_user": getattr(self.acting_user, "username", ""),
            "enabled": self.is_enabled,
            "secret": self.api_secret,
            "endpoints": {
                "rpc": endpoints.rpc_url,
                "events": endpoints.events_url,
                "manifest": endpoints.manifest_url,
            },
        }


__all__ = ["MCPServer", "MCPServerEndpoints"]
