from django.contrib import admin

from apps.rfids.models import RFID, RFIDSessionAttempt
from apps.core.admin import RFIDAdmin
from apps.locals.user_data import EntityModelAdmin


admin.site.register(RFID, RFIDAdmin)


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
