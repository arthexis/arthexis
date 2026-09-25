import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.errors import ConnectionClosed


def test_close_rejects_pending_calls() -> None:
    pending_calls = PendingCalls()
    unique_id, future = pending_calls.open()

    pending_calls.close()

    with pytest.raises(ConnectionClosed):
        async_to_sync(pending_calls.wait)(unique_id, future, timeout=1)
