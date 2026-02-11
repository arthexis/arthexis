"""Charging, monitoring, variables, and reporting result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def get_composite_schedule(ctx: HandlerContext) -> bool:
    """Handle GetCompositeSchedule responses.

    Expected payload keys: ``status``, ``scheduleStart``, and ``chargingSchedule``.
    Persistence updates: updates ``PowerProjection`` schedule fields and raw response.
    """

    return await legacy.handle_get_composite_schedule_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_charging_profile(ctx: HandlerContext) -> bool:
    """Handle SetChargingProfile responses.

    Expected payload keys: ``status``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_set_charging_profile_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def clear_charging_profile(ctx: HandlerContext) -> bool:
    """Handle ClearChargingProfile responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates matching ``ChargingProfile`` response status fields.
    """

    return await legacy.handle_clear_charging_profile_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_variables(ctx: HandlerContext) -> bool:
    """Handle GetVariables responses.

    Expected payload keys: ``getVariableResult`` entries with component/variable identifiers.
    Persistence updates: upserts ``Variable`` rows for returned attributes.
    """

    return await legacy.handle_get_variables_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_variables(ctx: HandlerContext) -> bool:
    """Handle SetVariables responses.

    Expected payload keys: ``setVariableResult`` entries.
    Persistence updates: upserts ``Variable`` rows using request metadata as value source.
    """

    return await legacy.handle_set_variables_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_variable_monitoring(ctx: HandlerContext) -> bool:
    """Handle SetVariableMonitoring responses.

    Expected payload keys: ``setMonitoringResult`` entries.
    Persistence updates: upserts ``MonitoringRule`` rows and links variables.
    """

    return await legacy.handle_set_variable_monitoring_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def clear_variable_monitoring(ctx: HandlerContext) -> bool:
    """Handle ClearVariableMonitoring responses.

    Expected payload keys: ``status``.
    Persistence updates: disables matching ``MonitoringRule`` rows when accepted.
    """

    return await legacy.handle_clear_variable_monitoring_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_monitoring_report(ctx: HandlerContext) -> bool:
    """Handle GetMonitoringReport responses.

    Expected payload keys: ``status``.
    Persistence updates: clears pending report requests on rejection status.
    """

    return await legacy.handle_get_monitoring_report_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_monitoring_base(ctx: HandlerContext) -> bool:
    """Handle SetMonitoringBase responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_set_monitoring_base_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_monitoring_level(ctx: HandlerContext) -> bool:
    """Handle SetMonitoringLevel responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: log and pending call result.
    """

    return await legacy.handle_set_monitoring_level_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def clear_display_message(ctx: HandlerContext) -> bool:
    """Handle ClearDisplayMessage responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates display message compliance tracking in store.
    """

    return await legacy.handle_clear_display_message_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def customer_information(ctx: HandlerContext) -> bool:
    """Handle CustomerInformation responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: logs response and records pending call result.
    """

    return await legacy.handle_customer_information_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_base_report(ctx: HandlerContext) -> bool:
    """Handle GetBaseReport responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: report request queue status updates in store.
    """

    return await legacy.handle_get_base_report_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_charging_profiles(ctx: HandlerContext) -> bool:
    """Handle GetChargingProfiles responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates charging profile report queue state.
    """

    return await legacy.handle_get_charging_profiles_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_display_messages(ctx: HandlerContext) -> bool:
    """Handle GetDisplayMessages responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: display message request queue updates in store.
    """

    return await legacy.handle_get_display_messages_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_report(ctx: HandlerContext) -> bool:
    """Handle GetReport responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: monitoring/device report queue updates in store.
    """

    return await legacy.handle_get_report_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def set_display_message(ctx: HandlerContext) -> bool:
    """Handle SetDisplayMessage responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: display message compliance updates in store.
    """

    return await legacy.handle_set_display_message_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_get_composite_schedule_result = legacy_adapter(get_composite_schedule)
handle_set_charging_profile_result = legacy_adapter(set_charging_profile)
handle_clear_charging_profile_result = legacy_adapter(clear_charging_profile)
handle_get_variables_result = legacy_adapter(get_variables)
handle_set_variables_result = legacy_adapter(set_variables)
handle_set_variable_monitoring_result = legacy_adapter(set_variable_monitoring)
handle_clear_variable_monitoring_result = legacy_adapter(clear_variable_monitoring)
handle_get_monitoring_report_result = legacy_adapter(get_monitoring_report)
handle_set_monitoring_base_result = legacy_adapter(set_monitoring_base)
handle_set_monitoring_level_result = legacy_adapter(set_monitoring_level)
handle_clear_display_message_result = legacy_adapter(clear_display_message)
handle_customer_information_result = legacy_adapter(customer_information)
handle_get_base_report_result = legacy_adapter(get_base_report)
handle_get_charging_profiles_result = legacy_adapter(get_charging_profiles)
handle_get_display_messages_result = legacy_adapter(get_display_messages)
handle_get_report_result = legacy_adapter(get_report)
handle_set_display_message_result = legacy_adapter(set_display_message)
