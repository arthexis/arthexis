import contextlib
import logging
import os
import shutil

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin
from .models import (
    PlaywrightBrowser,
    PlaywrightEngineFeatureDisabledError,
    PlaywrightRuntimeDisabledError,
    SessionCookie,
)

logger = logging.getLogger(__name__)


@admin.register(PlaywrightBrowser)
class PlaywrightBrowserAdmin(admin.ModelAdmin):
    list_display = ("name", "engine", "mode", "is_default")
    list_filter = ("engine", "mode", "is_default")
    search_fields = ("name", "binary_path")
    actions = ["test_browsers"]

    @admin.action(description=_("Test selected browser"))
    def test_browsers(self, request, queryset):
        for browser in queryset:
            try:
                driver = browser.create_driver()
            except (PlaywrightEngineFeatureDisabledError, PlaywrightRuntimeDisabledError) as exc:
                self.message_user(request, str(exc), level=messages.WARNING)
                continue
            except Exception as exc:  # pragma: no cover
                logger.exception("Unable to start browser %s", browser)
                self.message_user(request, _("Failed to start %(browser)s: %(error)s") % {"browser": browser, "error": exc}, level=messages.ERROR)
                continue
            with contextlib.suppress(Exception):
                driver.quit()
            note = ""
            if browser.mode == PlaywrightBrowser.Mode.HEADED and not os.environ.get("DISPLAY"):
                note = " " + str(_("DISPLAY is not set; consider headless mode."))
            if browser.binary_path and not shutil.which(browser.binary_path):
                note += " " + str(_("Configured binary path was not found in PATH."))
            self.message_user(request, _("%(browser)s started successfully.") % {"browser": browser} + note, level=messages.SUCCESS)


@admin.register(SessionCookie)
class SessionCookieAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner_display", "source", "state", "last_used_at", "last_validated_at", "rejection_count")
    list_filter = ("state", "source")
    search_fields = ("name", "source", "last_rejection_reason")
    exclude = ("cookies",)
