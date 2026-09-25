import pytest

from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import ConnectionClosed


@pytest.mark.asyncio
async def test_close_rejects_pending_calls() -> None:
    pending_calls = PendingCalls()
    unique_id, future = pending_calls.open()

    pending_calls.close()

    with pytest.raises(ConnectionClosed):
        await pending_calls.wait(unique_id, future, timeout=1)
