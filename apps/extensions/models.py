"""Models for hosted JavaScript browser extensions."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class JsExtension(Entity):
    """Definition for a hosted browser extension and its scripts."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=50, default="0.1.0")
    manifest_version = models.PositiveSmallIntegerField(default=3)
    is_enabled = models.BooleanField(default=True)
    matches = models.TextField(
        blank=True,
        help_text="Newline-separated match patterns for content scripts.",
    )
    content_script = models.TextField(blank=True)
    background_script = models.TextField(blank=True)
    options_page = models.TextField(blank=True)
    permissions = models.TextField(blank=True, help_text="Newline-separated permissions.")
    host_permissions = models.TextField(
        blank=True, help_text="Newline-separated host permissions (MV3)."
    )

    class Meta:
        verbose_name = _("JS Extension")
        verbose_name_plural = _("JS Extensions")
        ordering = ("name",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(manifest_version__in=(2, 3)),
                name="extensions_js_extension_manifest_version_valid",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def clean(self) -> None:
        """Validate supported manifest versions."""
        super().clean()
        if self.manifest_version not in {2, 3}:
            raise ValidationError({"manifest_version": "Manifest version must be 2 or 3."})

    @staticmethod
    def _split_lines(value: str) -> list[str]:
        """Split newline-separated fields into cleaned lists."""
        return [line.strip() for line in value.splitlines() if line.strip()]

    @property
    def match_patterns(self) -> list[str]:
        """Return match patterns for content scripts."""
        return self._split_lines(self.matches)

    @property
    def permission_list(self) -> list[str]:
        """Return extension permissions as a list."""
        return self._split_lines(self.permissions)

    @property
    def host_permission_list(self) -> list[str]:
        """Return host permissions as a list."""
        return self._split_lines(self.host_permissions)

    def build_manifest(self) -> dict:
        """Return a manifest.json payload for this extension."""
        manifest: dict[str, object] = {
            "manifest_version": self.manifest_version,
            "name": self.name,
            "version": self.version,
            "description": self.description or "",
        }

        if self.manifest_version >= 3:
            manifest["action"] = {"default_title": self.name}
        else:
            manifest["browser_action"] = {"default_title": self.name}

        if self.match_patterns:
            manifest["content_scripts"] = [
                {"matches": self.match_patterns, "js": ["content.js"]}
            ]

        if self.background_script:
            if self.manifest_version >= 3:
                manifest["background"] = {"service_worker": "background.js"}
            else:
                manifest["background"] = {
                    "scripts": ["background.js"],
                    "persistent": False,
                }

        if self.options_page:
            if self.manifest_version >= 3:
                manifest["options_ui"] = {"page": "options.html"}
            else:
                manifest["options_page"] = "options.html"

        if self.permission_list:
            manifest["permissions"] = self.permission_list

        if self.host_permission_list and self.manifest_version >= 3:
            manifest["host_permissions"] = self.host_permission_list

        return manifest

    def build_content_script_payload(self) -> str:
        """Return the content script with Arthexis-detection bootstrap code."""
        bootstrap_script = """
(function () {
  var badgeId = "arthexis-extension-status";

  function containsArthexisText() {
    if (!document || !document.body) {
      return false;
    }
    var bodyText = (document.body.innerText || "").toLowerCase();
    return bodyText.indexOf("arthexis") !== -1;
  }

  function hasArthexisMeta() {
    var generator = document.querySelector('meta[name="generator"]');
    if (generator && /arthexis/i.test(generator.getAttribute("content") || "")) {
      return true;
    }

    var appMeta = document.querySelector('meta[name="application-name"]');
    if (appMeta && /arthexis/i.test(appMeta.getAttribute("content") || "")) {
      return true;
    }

    return false;
  }

  function isArthexisInstance() {
    return hasArthexisMeta() || containsArthexisText();
  }

  function renderBadge() {
    var existing = document.getElementById(badgeId);
    if (existing) {
      existing.remove();
    }

    var isArthexis = isArthexisInstance();
    var badge = document.createElement("div");
    badge.id = badgeId;
    badge.textContent = isArthexis
      ? "Arthexis site detected"
      : "Not an Arthexis site";
    badge.setAttribute("data-arthexis-instance", isArthexis ? "true" : "false");
    badge.style.cssText = [
      "position:fixed",
      "top:12px",
      "right:12px",
      "z-index:2147483647",
      "padding:8px 10px",
      "border-radius:999px",
      "font:600 12px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif",
      "color:#fff",
      "background:" + (isArthexis ? "#1b7f3b" : "#9b1c1c"),
      "box-shadow:0 2px 8px rgba(0,0,0,0.25)",
      "pointer-events:none"
    ].join(";");

    document.documentElement.appendChild(badge);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderBadge, { once: true });
  } else {
    renderBadge();
  }
})();
""".strip()

        parts = [bootstrap_script]
        if self.content_script.strip():
            parts.append(self.content_script.strip())
        return "\n\n".join(parts) + "\n"

    def build_extension_archive_files(self) -> dict[str, str]:
        """Return the generated extension files keyed by archive filename."""
        files = {
            "manifest.json": self.build_manifest(),
            "content.js": self.build_content_script_payload(),
        }

        if self.background_script.strip():
            files["background.js"] = self.background_script
        if self.options_page.strip():
            files["options.html"] = self.options_page

        return files


__all__ = ["JsExtension"]
