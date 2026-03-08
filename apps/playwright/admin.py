import contextlib
import logging
import os
import shutil

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from apps.core.admin import OwnableAdminMixin
from .models import (
    PlaywrightBrowser,
    PlaywrightScript,
    SessionCookie,
    WebsiteScreenshotRun,
    WebsiteScreenshotSchedule,
    execute_website_screenshot_schedule,
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


@admin.register(PlaywrightScript)
class PlaywrightScriptAdmin(admin.ModelAdmin):
    list_display = ("name", "start_url", "python_path")
    search_fields = ("name", "python_path", "description")
    actions = ["execute_with_default_browser"]

    @admin.action(description=_("Execute using default browser"))
    def execute_with_default_browser(self, request, queryset):
        browser = PlaywrightBrowser.default()
        if browser is None:
            self.message_user(request, _("No default Playwright browser is configured."), level=messages.ERROR)
            return
        executed = 0
        for script in queryset:
            try:
                script.execute(browser=browser)
                executed += 1
            except Exception as exc:  # pragma: no cover
                logger.exception("Failed to execute script %s", script)
                self.message_user(request, _("Failed to execute %(script)s: %(error)s") % {"script": script, "error": exc}, level=messages.ERROR)
        if executed:
            self.message_user(
                request,
                ngettext("Executed %(count)d Playwright script.", "Executed %(count)d Playwright scripts.", executed) % {"count": executed},
                level=messages.SUCCESS,
            )


@admin.register(SessionCookie)
class SessionCookieAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner_display", "source", "state", "last_used_at", "last_validated_at", "rejection_count")
    list_filter = ("state", "source")
    search_fields = ("name", "source", "last_rejection_reason")


class WebsiteScreenshotRunInline(admin.TabularInline):
    model = WebsiteScreenshotRun
    extra = 0
    readonly_fields = ("document", "content_sample", "created_at")


@admin.register(WebsiteScreenshotSchedule)
class WebsiteScreenshotScheduleAdmin(admin.ModelAdmin):
    list_display = ("slug", "label", "url", "is_active", "sampling_period_minutes", "last_sampled_at", "favored_engine")
    list_filter = ("is_active", "favored_engine")
    search_fields = ("slug", "label", "url")
    inlines = (WebsiteScreenshotRunInline,)
    actions = ("run_now",)

    @admin.action(description=_("Run screenshot schedule now"))
    def run_now(self, request, queryset):
        successes = 0
        for schedule in queryset:
            try:
                execute_website_screenshot_schedule(schedule, user=request.user)
            except Exception as exc:
                self.message_user(request, _("Failed %(slug)s: %(error)s") % {"slug": schedule.slug, "error": exc}, level=messages.ERROR)
                continue
            successes += 1
        if successes:
            self.message_user(
                request,
                ngettext("Executed %(count)d schedule.", "Executed %(count)d schedules.", successes) % {"count": successes},
                level=messages.SUCCESS,
            )
