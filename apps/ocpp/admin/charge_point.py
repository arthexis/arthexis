from .common_imports import *

from .charger import *  # noqa: F401,F403
from .cp_reservation import *  # noqa: F401,F403
from .transaction import *  # noqa: F401,F403
from .meter_value import *  # noqa: F401,F403
from .security_event import *  # noqa: F401,F403
from .charger_log_request import *  # noqa: F401,F403
from .cp_forwarder import *  # noqa: F401,F403


@admin.register(RFIDSessionAttempt)
class RFIDSessionAttemptAdmin(EntityModelAdmin):
    list_display = (
        "rfid",
        "status",
        "charger",
        "account",
        "transaction",
        "attempted_at",
    )
    list_filter = ("status",)
    search_fields = (
        "rfid",
        "charger__charger_id",
        "account__name",
        "transaction__ocpp_id",
    )
    readonly_fields = ("attempted_at",)


@admin.register(StationModel)
class StationModelAdmin(EntityModelAdmin):
    list_display = (
        "vendor",
        "model_family",
        "model",
        "preferred_ocpp_version",
        "max_power_kw",
        "max_voltage_v",
    )
    search_fields = ("vendor", "model_family", "model")
    list_filter = ("preferred_ocpp_version",)
