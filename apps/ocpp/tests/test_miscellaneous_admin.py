from django.contrib import admin

from apps.ocpp.admin.miscellaneous import messages_admin
from apps.ocpp.models import (
    CustomerInformationChunk,
    CustomerInformationRequest,
    DataTransferMessage,
    DisplayMessage,
    DisplayMessageNotification,
)


def test_message_admin_records_are_registered_from_focused_module() -> None:
    expected_admins = {
        DataTransferMessage: messages_admin.DataTransferMessageAdmin,
        CustomerInformationRequest: messages_admin.CustomerInformationRequestAdmin,
        CustomerInformationChunk: messages_admin.CustomerInformationChunkAdmin,
        DisplayMessageNotification: messages_admin.DisplayMessageNotificationAdmin,
        DisplayMessage: messages_admin.DisplayMessageAdmin,
    }

    for model, admin_class in expected_admins.items():
        assert isinstance(admin.site._registry[model], admin_class)
