import contextlib
import logging
import os
import shutil

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from .models import SeleniumBrowser, SeleniumScript

logger = logging.getLogger(__name__)


@admin.register(SeleniumBrowser)
class SeleniumBrowserAdmin(admin.ModelAdmin):
    list_display = ("name", "engine", "mode", "is_default")
    list_filter = ("engine", "mode", "is_default")
    search_fields = ("name", "binary_path")
    actions = ["test_browsers"]

    def _connection_advice(self, browser: SeleniumBrowser) -> list[str]:
        advice: list[str] = []
        if browser.engine == SeleniumBrowser.Engine.FIREFOX:
            if browser.mode == SeleniumBrowser.Mode.HEADED and not os.environ.get(
                "DISPLAY"
            ):
                advice.append(
                    _("Set DISPLAY or switch the browser to headless mode for remote use.")
                )
            if browser.binary_path and not shutil.which(browser.binary_path):
                advice.append(_("Verify the Firefox binary path or install the browser."))
        return advice

    @admin.action(description=_("Test selected browser"))
    def test_browsers(self, request, queryset):
        for browser in queryset:
            advice = self._connection_advice(browser)
            try:
                driver = browser.create_driver()
            except Exception as exc:  # pragma: no cover - Selenium may not be available
                logger.exception("Unable to start browser %s", browser)
                message = _("Failed to start %(browser)s: %(error)s") % {
                    "browser": browser,
                    "error": exc,
                }
                if advice:
                    message += " " + _("Tips: %(tips)s") % {
                        "tips": " ".join(map(str, advice)),
                    }
                self.message_user(request, message, level=messages.ERROR)
                continue

            with contextlib.suppress(Exception):
                driver.quit()

            success = _("%(browser)s started successfully.") % {"browser": browser}
            if advice:
                success += " " + _("Connection notes: %(tips)s") % {
                    "tips": " ".join(map(str, advice)),
                }
            self.message_user(request, success, level=messages.SUCCESS)


@admin.register(SeleniumScript)
class SeleniumScriptAdmin(admin.ModelAdmin):
    list_display = ("name", "start_url", "python_path")
    search_fields = ("name", "python_path", "description")
    actions = ["execute_with_default_browser"]

    @admin.action(description=_("Execute using default browser"))
    def execute_with_default_browser(self, request, queryset):
        browser = SeleniumBrowser.default()
        if browser is None:
            self.message_user(
                request,
                _("No default Selenium browser is configured."),
                level=messages.ERROR,
            )
            return

        executed = 0
        for script in queryset:
            try:
                script.execute(browser=browser)
            except Exception as exc:  # pragma: no cover - execution depends on Selenium
                logger.exception("Failed to execute script %s", script)
                self.message_user(
                    request,
                    _("Failed to execute %(script)s: %(error)s")
                    % {"script": script, "error": exc},
                    level=messages.ERROR,
                )
            else:
                executed += 1

        if executed:
            self.message_user(
                request,
                ngettext(
                    "Executed %(count)d Selenium script.",
                    "Executed %(count)d Selenium scripts.",
                    executed,
                )
                % {"count": executed},
                level=messages.SUCCESS,
            )
