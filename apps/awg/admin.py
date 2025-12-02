from django.contrib import admin
from django import forms

from .constants import CONDUIT_CHOICES
from .models import CableSize, ConduitFill, CalculatorTemplate, PowerLead
from apps.locals.user_data import EntityModelAdmin


@admin.register(CableSize)
class CableSizeAdmin(EntityModelAdmin):
    list_display = (
        "awg_size",
        "material",
        "area_kcmil",
        "amps_60c",
        "amps_75c",
        "amps_90c",
        "line_num",
    )
    search_fields = ("awg_size", "material")


@admin.register(ConduitFill)
class ConduitFillAdmin(EntityModelAdmin):
    list_display = ("trade_size", "conduit", "awg_8", "awg_6", "awg_4", "awg_2")
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
        choices=CONDUIT_CHOICES,
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
class CalculatorTemplateAdmin(EntityModelAdmin):
    form = CalculatorTemplateForm
    list_display = (
        "name",
        "description",
        "public",
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

    @admin.display(boolean=True, description="Public", ordering="show_in_pages")
    def public(self, obj):
        return obj.show_in_pages

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


@admin.register(PowerLead)
class PowerLeadAdmin(EntityModelAdmin):
    list_display = (
        "created_on",
        "user",
        "status",
        "assign_to",
        "ip_address",
        "malformed",
    )
    list_filter = ("status", "malformed")
    search_fields = ("user__username", "ip_address")
    raw_id_fields = ("assign_to",)
    readonly_fields = (
        "created_on",
        "user",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "values",
        "malformed",
    )

