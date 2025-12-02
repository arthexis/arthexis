from django.contrib import admin

from apps.cards.models import RFID
from apps.core.admin import RFIDAdmin


admin.site.register(RFID, RFIDAdmin)
