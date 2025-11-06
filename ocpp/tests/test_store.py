import time

from django.utils import timezone

from ocpp import store


def _prepare_log(serial: str) -> str:
    key = store.identity_key(serial, None)
    store.clear_log(key, log_type="charger")
    return key


def test_schedule_call_timeout_logs_after_delay():
    serial = "SCHED-LOG"
    message_id = "timeout-log"
    log_key = _prepare_log(serial)
    store.register_pending_call(
        message_id,
        {
            "action": "TestAction",
            "charger_id": serial,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    try:
        store.schedule_call_timeout(
            message_id,
            timeout=0.05,
            action="TestAction",
            log_key=log_key,
            message="Custom timeout label",
        )
        time.sleep(0.15)
        log_entries = store.get_logs(log_key, log_type="charger")
        assert any("Custom timeout label" in entry for entry in log_entries)
    finally:
        store.clear_pending_calls(serial)
        store.clear_log(log_key, log_type="charger")


def test_schedule_call_timeout_cancelled_on_response():
    serial = "SCHED-CANCEL"
    message_id = "timeout-cancel"
    log_key = _prepare_log(serial)
    store.register_pending_call(
        message_id,
        {
            "action": "TestAction",
            "charger_id": serial,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    try:
        store.schedule_call_timeout(
            message_id,
            timeout=0.2,
            action="TestAction",
            log_key=log_key,
            message="Should not be logged",
        )
        metadata = store.pop_pending_call(message_id)
        assert metadata is not None
        store.record_pending_call_result(message_id, metadata=metadata)
        time.sleep(0.25)
        log_entries = store.get_logs(log_key, log_type="charger")
        assert all("Should not be logged" not in entry for entry in log_entries)
    finally:
        store.clear_pending_calls(serial)
        store.clear_log(log_key, log_type="charger")
