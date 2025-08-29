from django.contrib import admin
from django import forms

from .models import CableSize, ConduitFill, CalculatorTemplate


@admin.register(CableSize)
class CableSizeAdmin(admin.ModelAdmin):
    list_display = (
        "awg_size",
        "material",
        "area_kcmil",
        "amps_60c",
        "line_num",
    )
    search_fields = ("awg_size", "material")


@admin.register(ConduitFill)
class ConduitFillAdmin(admin.ModelAdmin):
    list_display = ("trade_size", "conduit")
    search_fields = ("trade_size", "conduit")


class CalculatorTemplateForm(forms.ModelForm):
    """Admin form mirroring the calculator's select inputs."""

    material = forms.ChoiceField(
        choices=[("cu", "Copper (cu)"), ("al", "Aluminum (al)")],
        required=False,
    )
    max_lines = forms.TypedChoiceField(
        choices=[(1, "1"), (2, "2"), (3, "3"), (4, "4")],
        coerce=int,
        required=False,
    )
    phases = forms.TypedChoiceField(
        choices=[
            (2, "AC Two Phases (2)"),
            (1, "AC Single Phase (1)"),
            (3, "AC Three Phases (3)"),
        ],
        coerce=int,
        required=False,
    )
    temperature = forms.TypedChoiceField(
        choices=[(60, "60C (140F)"), (75, "75C (167F)"), (90, "90C (194F)")],
        coerce=int,
        required=False,
    )
    conduit = forms.ChoiceField(
        choices=[
            ("emt", "EMT"),
            ("imc", "IMC"),
            ("rmc", "RMC"),
            ("fmc", "FMC"),
        ],
        required=False,
    )
    ground = forms.TypedChoiceField(
        choices=[(1, "1"), (0, "0")],
        coerce=int,
        required=False,
    )

    class Meta:
        model = CalculatorTemplate
        fields = "__all__"


@admin.register(CalculatorTemplate)
class CalculatorTemplateAdmin(admin.ModelAdmin):
    form = CalculatorTemplateForm
    list_display = (
        "name",
        "description",
        "show_in_pages",
        "meters",
        "amps",
        "volts",
        "material",
        "calculator_link",
    )
    actions = ["run_calculator"]
    readonly_fields = ("calculator_link",)
    fields = (
        "name",
        "description",
        "show_in_pages",
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
