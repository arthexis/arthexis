import logging

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from .models import SeleniumBrowser, SeleniumScript

logger = logging.getLogger(__name__)


@admin.register(SeleniumBrowser)
class SeleniumBrowserAdmin(admin.ModelAdmin):
    list_display = ("name", "engine", "mode", "is_default")
    list_filter = ("engine", "mode", "is_default")
    search_fields = ("name", "binary_path")


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
