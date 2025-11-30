from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from .models import Brand, ElectricVehicle, EVModel, WMICode


class WMICodeInline(admin.TabularInline):
    model = WMICode
    extra = 0


@admin.register(Brand)
class BrandAdmin(EntityModelAdmin):
    fields = ("name",)
    list_display = ("name", "wmi_codes_display")
    search_fields = ("name",)
    inlines = [WMICodeInline]

    def wmi_codes_display(self, obj):
        return ", ".join(obj.wmi_codes.values_list("code", flat=True))

    wmi_codes_display.short_description = "WMI codes"


@admin.register(EVModel)
class EVModelAdmin(EntityModelAdmin):
    fields = ("brand", "name")
    list_display = ("name", "brand", "brand_wmi_codes")
    search_fields = ("name", "brand__name")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("brand").prefetch_related("brand__wmi_codes")

    def brand_wmi_codes(self, obj):
        if not obj.brand:
            return ""
        codes = [wmi.code for wmi in obj.brand.wmi_codes.all()]
        return ", ".join(codes)

    brand_wmi_codes.short_description = "WMI codes"


@admin.register(ElectricVehicle)
class ElectricVehicleAdmin(EntityModelAdmin):
    list_display = ("vin", "model", "brand", "account")
    search_fields = ("vin", "license_plate", "brand__name", "model__name")
    autocomplete_fields = ("brand", "model", "account")
    ordering = ("vin",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.select_related("brand", "model", "account")
