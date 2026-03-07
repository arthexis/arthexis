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
    permissions = models.TextField(
        blank=True, help_text="Newline-separated permissions."
    )
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
            raise ValidationError(
                {"manifest_version": "Manifest version must be 2 or 3."}
            )

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

    @classmethod
    def github_resolve_comments_extension_defaults(cls) -> dict[str, str | int]:
        """Return default values for the GitHub resolve-comments helper extension."""
        return {
            "slug": "github-resolve-open-comments",
            "name": "GitHub Resolve Open Comments",
            "description": (
                "Adds developer tools on GitHub PRs and issues to resolve all open "
                "review conversations, with optional comment posting before resolve."
            ),
            "version": "1.0.0",
            "manifest_version": 3,
            "matches": "https://github.com/*",
            "permissions": "storage",
            "host_permissions": "https://github.com/*",
            "content_script": cls.github_resolve_comments_content_script(),
            "options_page": cls.github_resolve_comments_options_page(),
        }

    @staticmethod
    def github_resolve_comments_content_script() -> str:
        """Return content script that resolves all open GitHub comment threads."""
        return """
(function () {
  if (!/github\\.com$/i.test(window.location.hostname)) {
    return;
  }

  var panelId = "arthexis-resolve-comments-panel";
  var statusId = "arthexis-resolve-comments-status";

  function qsa(root, selector) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function findResolveButtons() {
    return qsa(document, "button").filter(function (button) {
      var text = (button.textContent || "").trim().toLowerCase();
      return text === "resolve conversation" && !button.disabled && button.offsetParent;
    });
  }

  function findContainer(button) {
    return button.closest(".review-comment, .js-resolvable-thread-form, .js-comment-container, .TimelineItem, .js-timeline-item");
  }

  function findCommentTextarea(container) {
    if (!container) {
      return null;
    }

    var area = container.querySelector("textarea[name='comment[body]'], textarea.js-comment-field, textarea[aria-label='Leave a comment']");
    if (!area || area.disabled || area.readOnly) {
      return null;
    }
    return area;
  }

  function findCommentButton(container) {
    if (!container) {
      return null;
    }

    var candidates = qsa(container, "button");
    for (var i = 0; i < candidates.length; i += 1) {
      var button = candidates[i];
      var label = ((button.textContent || "") + " " + (button.getAttribute("aria-label") || "")).toLowerCase();
      if (button.disabled) {
        continue;
      }
      if (label.indexOf("comment") !== -1 && label.indexOf("cancel") === -1) {
        return button;
      }
    }
    return null;
  }

  function triggerInput(element, value) {
    element.focus();
    element.value = value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function commentThenResolve(button, message) {
    var container = findContainer(button);
    var textarea = findCommentTextarea(container);
    var commentButton = findCommentButton(container);

    if (!textarea || !commentButton) {
      button.click();
      await sleep(250);
      return;
    }

    triggerInput(textarea, message);
    commentButton.click();
    await sleep(700);
    button.click();
    await sleep(250);
  }

  function getDefaultComment() {
    return new Promise(function (resolve) {
      if (!window.chrome || !chrome.storage || !chrome.storage.sync) {
        resolve("Resolved by Arthexis helper.");
        return;
      }

      chrome.storage.sync.get({ arthexisResolveComment: "Resolved by Arthexis helper." }, function (result) {
        resolve(result.arthexisResolveComment || "Resolved by Arthexis helper.");
      });
    });
  }

  function renderStatus(text, tone) {
    var status = document.getElementById(statusId);
    if (!status) {
      return;
    }
    status.textContent = text;
    status.style.color = tone === "error" ? "#f85149" : "#8b949e";
  }

  async function resolveAll(withComment) {
    var buttons = findResolveButtons();
    if (!buttons.length) {
      renderStatus("No open review conversations found on this page.");
      return;
    }

    renderStatus("Resolving " + buttons.length + " conversation(s)...");
    var comment = "";
    if (withComment) {
      comment = await getDefaultComment();
    }

    for (var i = 0; i < buttons.length; i += 1) {
      var button = buttons[i];
      if (!button || button.disabled || !button.offsetParent) {
        continue;
      }
      if (withComment) {
        await commentThenResolve(button, comment);
      } else {
        button.click();
        await sleep(250);
      }
    }

    renderStatus("Completed resolve-all action.");
  }

  function addButtonRow(panel) {
    var row = document.createElement("div");
    row.style.display = "flex";
    row.style.gap = "8px";
    row.style.marginTop = "8px";

    var resolveButton = document.createElement("button");
    resolveButton.type = "button";
    resolveButton.textContent = "Resolve all open comments";
    resolveButton.style.cssText = "padding:6px 10px;background:#238636;color:#fff;border:1px solid rgba(240,246,252,.1);border-radius:6px;cursor:pointer;font-size:12px;";
    resolveButton.addEventListener("click", function () {
      resolveAll(false).catch(function (error) {
        renderStatus("Resolve action failed: " + error.message, "error");
      });
    });

    var resolveWithCommentButton = document.createElement("button");
    resolveWithCommentButton.type = "button";
    resolveWithCommentButton.textContent = "Resolve all with comment";
    resolveWithCommentButton.style.cssText = "padding:6px 10px;background:#1f6feb;color:#fff;border:1px solid rgba(240,246,252,.1);border-radius:6px;cursor:pointer;font-size:12px;";
    resolveWithCommentButton.addEventListener("click", function () {
      resolveAll(true).catch(function (error) {
        renderStatus("Resolve with comment failed: " + error.message, "error");
      });
    });

    row.appendChild(resolveButton);
    row.appendChild(resolveWithCommentButton);
    panel.appendChild(row);
  }

  function mountPanel() {
    if (document.getElementById(panelId)) {
      return;
    }

    var panel = document.createElement("div");
    panel.id = panelId;
    panel.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:2147483647;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;padding:10px;box-shadow:0 8px 24px rgba(1,4,9,.4);font:12px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;max-width:360px;";

    var title = document.createElement("div");
    title.textContent = "Arthexis GitHub tools";
    title.style.fontWeight = "600";
    panel.appendChild(title);

    var subtitle = document.createElement("div");
    subtitle.textContent = "Bulk resolve open review comments on this page.";
    subtitle.style.marginTop = "4px";
    subtitle.style.color = "#8b949e";
    panel.appendChild(subtitle);

    addButtonRow(panel);

    var status = document.createElement("div");
    status.id = statusId;
    status.style.marginTop = "8px";
    status.style.color = "#8b949e";
    status.textContent = "Ready.";
    panel.appendChild(status);

    document.body.appendChild(panel);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountPanel, { once: true });
  } else {
    mountPanel();
  }
})();
""".strip()

    @staticmethod
    def github_resolve_comments_options_page() -> str:
        """Return options page used to configure resolve-comment text."""
        return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Arthexis GitHub Resolve Comments</title>
  </head>
  <body>
    <main>
      <h1>Resolve Comments Settings</h1>
      <label for="comment">Default comment text</label>
      <br />
      <textarea id="comment" rows="5" cols="60"></textarea>
      <br />
      <button id="save" type="button">Save</button>
      <p id="status"></p>
    </main>
    <script>
      const commentElement = document.getElementById("comment");
      const statusElement = document.getElementById("status");
      const saveElement = document.getElementById("save");
      const fallback = "Resolved by Arthexis helper.";

      function loadSettings() {
        if (!window.chrome || !chrome.storage || !chrome.storage.sync) {
          commentElement.value = fallback;
          return;
        }
        chrome.storage.sync.get({ arthexisResolveComment: fallback }, (result) => {
          commentElement.value = result.arthexisResolveComment || fallback;
        });
      }

      function saveSettings() {
        const value = commentElement.value.trim() || fallback;
        if (!window.chrome || !chrome.storage || !chrome.storage.sync) {
          statusElement.textContent = "Saved in this session only.";
          return;
        }
        chrome.storage.sync.set({ arthexisResolveComment: value }, () => {
          statusElement.textContent = "Saved.";
        });
      }

      saveElement.addEventListener("click", saveSettings);
      loadSettings();
    </script>
  </body>
</html>
""".strip()

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

    def build_extension_archive_files(self) -> dict[str, str | dict[str, object]]:
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
