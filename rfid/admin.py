from django.contrib import admin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import RFID


class RFIDResource(resources.ModelResource):
    class Meta:
        model = RFID
        fields = ("rfid", "allowed")


@admin.register(RFID)
class RFIDAdmin(ImportExportModelAdmin):
    resource_class = RFIDResource
    list_display = ("rfid", "accounts_display", "allowed", "added_on")

    def accounts_display(self, obj):
        return ", ".join(str(a) for a in obj.accounts.all())

    accounts_display.short_description = "Accounts"
