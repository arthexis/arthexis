from django.contrib import admin, messages

from .models import Pattern, Sample
from django.contrib import admin

from .models import Sample


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ("created_at", "short_content")
    readonly_fields = ("created_at",)

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

