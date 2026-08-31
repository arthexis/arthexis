from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.ocpp.admin.charge_point.actions.services import ActionServiceMixin
from apps.ocpp.models import Charger, ControlOperationEvent


class _ActionServiceHarness(ActionServiceMixin):
    pass


class ControlOperationEventTests(TestCase):
    def test_log_control_operation_creates_event(self):
        user = get_user_model().objects.create_user(username="operator", password="secret")
        charger = Charger.objects.create(charger_id="CP-EVENT-1", connector_id=1)
        request = RequestFactory().get("/")
        request.user = user

        harness = _ActionServiceHarness()
        harness._log_control_operation(
            request,
            charger=charger,
            action="TriggerMessage",
            transport=ControlOperationEvent.Transport.LOCAL,
            status=ControlOperationEvent.Status.FAILED,
            detail="No active websocket connection",
            request_payload={"requestedMessage": "StatusNotification"},
        )

        event = ControlOperationEvent.objects.get(charger=charger)
        self.assertEqual(event.action, "TriggerMessage")
        self.assertEqual(event.status, ControlOperationEvent.Status.FAILED)
        self.assertEqual(event.actor, user)

    def test_log_control_operation_redacts_idtag_values(self):
        charger = Charger.objects.create(charger_id="CP-EVENT-2", connector_id=1)
        request = RequestFactory().get("/")

        harness = _ActionServiceHarness()
        harness._log_control_operation(
            request,
            charger=charger,
            action="SendLocalList",
            transport=ControlOperationEvent.Transport.LOCAL,
            status=ControlOperationEvent.Status.SENT,
            request_payload={
                "listVersion": 5,
                "localAuthorizationList": [
                    {"idTag": "TAG-1234", "idTagInfo": {"status": "Accepted"}},
                    {"idTag": "TAG-5678", "idTagInfo": {"status": "Accepted"}},
                ],
            },
        )

        event = ControlOperationEvent.objects.get(charger=charger)
        self.assertEqual(event.request_payload["listVersion"], 5)
        self.assertEqual(
            event.request_payload["localAuthorizationList"],
            [
                {"idTag": "***redacted***", "idTagInfo": {"status": "Accepted"}},
                {"idTag": "***redacted***", "idTagInfo": {"status": "Accepted"}},
            ],
        )
