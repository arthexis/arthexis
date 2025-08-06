from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

import pyperclip
from pyperclip import PyperclipException

from .models import Pattern, Sample


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ("created_at", "short_content")
    readonly_fields = ("created_at",)
    change_list_template = "admin/clipboard/sample/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "from-clipboard/",
                self.admin_site.admin_view(self.add_from_clipboard),
                name="clipboard_sample_from_clipboard",
            )
        ]
        return custom + urls

    def add_from_clipboard(self, request):
        try:
            content = pyperclip.paste()
        except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
            self.message_user(request, f"Clipboard error: {exc}", level=messages.ERROR)
            return redirect("..")
        if not content:
            self.message_user(request, "Clipboard is empty.", level=messages.INFO)
            return redirect("..")
        Sample.objects.create(content=content)
        self.message_user(request, "Sample added from clipboard.", level=messages.SUCCESS)
        return redirect("..")

    def short_content(self, obj):
        return obj.content[:50]

    short_content.short_description = "Content"


@admin.register(Pattern)
class PatternAdmin(admin.ModelAdmin):
    list_display = ("mask", "priority")
    actions = ["scan_latest_sample"]

    @admin.action(description="Scan latest sample")
    def scan_latest_sample(self, request, queryset):
        sample = Sample.objects.first()
        if not sample:
            self.message_user(request, "No samples available.", level=messages.WARNING)
            return
        for pattern in Pattern.objects.order_by("-priority", "id"):
            substitutions = pattern.match(sample.content)
            if substitutions is not None:
                if substitutions:
                    details = ", ".join(
                        f"[{k}] -> '{v}'" for k, v in substitutions.items()
                    )
                    msg = f"Matched '{pattern.mask}' with substitutions: {details}"
                else:
                    msg = f"Matched '{pattern.mask}' with no substitutions"
                self.message_user(request, msg, level=messages.SUCCESS)
                return
        self.message_user(
            request,
            "No pattern matched the latest sample.",
            level=messages.INFO,
        )

