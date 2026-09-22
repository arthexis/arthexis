from unittest import IsolatedAsyncioTestCase

from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import ConnectionClosed


class CorrelationTests(IsolatedAsyncioTestCase):
    async def test_close_rejects_pending_calls(self) -> None:
        pending_calls = PendingCalls()
        unique_id, future = pending_calls.open()

        pending_calls.close()

        with self.assertRaises(ConnectionClosed):
            await pending_calls.wait(unique_id, future, timeout=1)
