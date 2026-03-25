"""Focused CSMS action handlers with small async interfaces."""

from __future__ import annotations

from apps.ocpp.consumers.csms.actions.authorization import AuthorizationActionHandler
from apps.ocpp.consumers.csms.actions.charging_limits import (
    ClearedChargingLimitActionHandler,
    NotifyChargingLimitActionHandler,
)
from apps.ocpp.consumers.csms.actions.display_messages import (
    NotifyDisplayMessagesActionHandler,
)
from apps.ocpp.consumers.csms.actions.monitoring_reports import (
    NotifyMonitoringReportActionHandler,
)


def build_action_handlers(consumer) -> dict[str, object]:
    """Return focused CSMS action handlers keyed by OCPP action name."""

    return {
        "Authorize": AuthorizationActionHandler(consumer),
        "ClearedChargingLimit": ClearedChargingLimitActionHandler(consumer),
        "NotifyChargingLimit": NotifyChargingLimitActionHandler(consumer),
        "NotifyDisplayMessages": NotifyDisplayMessagesActionHandler(consumer),
        "NotifyMonitoringReport": NotifyMonitoringReportActionHandler(consumer),
    }
