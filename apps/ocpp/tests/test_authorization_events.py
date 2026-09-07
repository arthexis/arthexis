from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase

from apps.ocpp.consumers.csms.actions.authorization import AuthorizationActionHandler


class AuthorizationEventTests(SimpleTestCase):
    async def test_authorize_emits_masked_result_event(self):
        account = object()
        decision = SimpleNamespace(
            status="Accepted",
            policy="registered",
            reason="allowed",
            log_unlinked_rfid=False,
        )
        consumer = SimpleNamespace(
            charger_id="gway-001",
            connector_value=1,
            _get_account=AsyncMock(return_value=account),
            _is_direct_ocpp_account=Mock(return_value=True),
            _evaluate_authorization_policy=AsyncMock(return_value=decision),
            _apply_rfid_authorization_side_effects=AsyncMock(
                return_value=(None, account)
            ),
            _record_rfid_attempt=AsyncMock(),
        )

        with patch(
            "apps.ocpp.consumers.csms.actions.authorization.aemit_event",
            new_callable=AsyncMock,
        ) as emit_event:
            response = await AuthorizationActionHandler(consumer).handle(
                {"idTag": "04A1B2C3"}, None, None, None
            )

        self.assertEqual(response, {"idTagInfo": {"status": "Accepted"}})
        emit_event.assert_awaited_once_with(
            "ocpp.authorization",
            charger_id="gway-001",
            connector_id=1,
            id_tag="****B2C3",
            status="Accepted",
            policy="registered",
            reason="allowed",
        )
