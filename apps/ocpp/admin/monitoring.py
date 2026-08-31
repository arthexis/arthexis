from .common_imports import *


@admin.register(Variable)
class VariableAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "component_name",
        "variable_name",
        "attribute_type",
        "attribute_status",
        "updated_on",
    )
    list_filter = ("component_name", "variable_name", "attribute_type", "attribute_status")
    search_fields = (
        "charger__charger_id",
        "component_name",
        "component_instance",
        "variable_name",
        "variable_instance",
        "value",
    )
    readonly_fields = (
        "charger",
        "component_name",
        "component_instance",
        "variable_name",
        "variable_instance",
        "attribute_type",
        "attribute_status",
        "value",
        "value_type",
        "created_on",
        "updated_on",
    )
    date_hierarchy = "updated_on"


@admin.register(MonitoringRule)
class MonitoringRuleAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "monitoring_id",
        "variable",
        "monitor_type",
        "severity",
        "is_active",
        "updated_on",
    )
    list_filter = ("monitor_type", "is_active", "severity")
    search_fields = (
        "charger__charger_id",
        "monitoring_id",
        "variable__component_name",
        "variable__variable_name",
    )
    readonly_fields = (
        "charger",
        "variable",
        "monitoring_id",
        "severity",
        "monitor_type",
        "threshold",
        "is_transaction",
        "is_active",
        "raw_payload",
        "created_on",
        "updated_on",
    )
    date_hierarchy = "updated_on"


@admin.register(MonitoringReport)
class MonitoringReportAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "seq_no",
        "generated_at",
        "tbc",
        "reported_at",
    )
    list_filter = ("tbc",)
    search_fields = ("charger__charger_id", "request_id")
    readonly_fields = (
        "charger",
        "request_id",
        "seq_no",
        "generated_at",
        "tbc",
        "raw_payload",
        "reported_at",
    )
    date_hierarchy = "reported_at"
