from django.contrib import admin

from .models import CableSize, ConduitFill, CalculatorTemplate


@admin.register(CableSize)
class CableSizeAdmin(admin.ModelAdmin):
    list_display = ("awg_size", "material", "line_num")
    search_fields = ("awg_size", "material")


@admin.register(ConduitFill)
class ConduitFillAdmin(admin.ModelAdmin):
    list_display = ("trade_size", "conduit")
    search_fields = ("trade_size", "conduit")


@admin.register(CalculatorTemplate)
class CalculatorTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "meters", "amps", "volts", "material", "calculator_link")
    actions = ["run_calculator"]
    readonly_fields = ("calculator_link",)
    fields = (
        "name",
        "meters",
        "amps",
        "volts",
        "material",
        "max_awg",
        "max_lines",
        "phases",
        "temperature",
        "conduit",
        "ground",
        "calculator_link",
    )

    def run_calculator(self, request, queryset):
        for template in queryset:
            result = template.run()
            awg = result.get("awg", "n/a")
            self.message_user(request, f"{template.name}: {awg}")

    run_calculator.short_description = "Run calculation"

    def calculator_link(self, obj):
        from django.utils.html import format_html

        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    calculator_link.short_description = "Calculator"
