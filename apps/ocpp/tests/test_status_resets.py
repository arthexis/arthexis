from datetime import timedelta
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock, patch

from django.db.utils import OperationalError
from django.test import TransactionTestCase
from django.utils import timezone

from apps.ocpp import status_resets
from apps.ocpp.models import Charger


class StatusResetTests(TransactionTestCase):
    reset_sequences = True

    def test_charger_table_check_handles_missing_table(self):
        connection = MagicMock()
        connection.introspection.table_names.return_value = []

        with patch.object(status_resets, "connections", {"default": connection}):
            assert status_resets._charger_table_exists() is False

    def test_clear_cached_statuses_returns_zero_when_lookup_fails(self):
        connection = MagicMock()
        connection.introspection.table_names.side_effect = OperationalError

        with patch.object(status_resets, "connections", {"default": connection}):
            cleared = status_resets.clear_cached_statuses()

        assert cleared == 0

    def test_clear_stale_cached_statuses_short_circuits_without_charger_table(self):
        with patch.object(status_resets, "_charger_table_exists", return_value=False):
            updated = status_resets.clear_stale_cached_statuses()

        assert updated == 0

    def test_clear_stale_cached_statuses_resets_expected_fields(self):
        now = timezone.now()
        stale_non_placeholder = Charger.objects.create(
            charger_id="ST-1",
            last_status="Faulted",
            last_status_vendor_info="vendor",
            last_status_timestamp=now,
            last_heartbeat=now - timedelta(minutes=10),
            last_error_code="E999",
        )
        stale_placeholder = Charger.objects.create(
            charger_id="ST-2",
            last_status="Available",
            last_status_vendor_info="vendor",
            last_status_timestamp=now,
            last_heartbeat=None,
            last_error_code="noerror",
        )
        fresh = Charger.objects.create(
            charger_id="FR-1",
            last_status="Charging",
            last_status_vendor_info="vendor",
            last_status_timestamp=now,
            last_heartbeat=now,
            last_error_code="SomeError",
        )

        updated = status_resets.clear_stale_cached_statuses(
            max_age=timedelta(minutes=5)
        )

        assert updated == 2

        stale_non_placeholder.refresh_from_db()
        assert stale_non_placeholder.last_status == ""
        assert stale_non_placeholder.last_status_vendor_info is None
        assert stale_non_placeholder.last_status_timestamp is None
        assert stale_non_placeholder.last_error_code == "E999"

        stale_placeholder.refresh_from_db()
        assert stale_placeholder.last_status == ""
        assert stale_placeholder.last_status_vendor_info is None
        assert stale_placeholder.last_status_timestamp is None
        assert stale_placeholder.last_error_code == ""

        fresh.refresh_from_db()
        assert fresh.last_status == "Charging"
        assert fresh.last_status_vendor_info == "vendor"
        assert fresh.last_status_timestamp == now
        assert fresh.last_error_code == "SomeError"

    def test_session_lock_cleanup_runs_for_expired_lock(self):
        now = timezone.now()
        lock_dir = Path(mkdtemp())
        lock_path = lock_dir / "charging.lck"
        lock_path.touch()
        past_time = (now - timedelta(minutes=10)).timestamp()
        lock_path = lock_path.resolve()

        # Update the timestamps using os.utime for compatibility
        import os

        os.utime(lock_path, (past_time, past_time))

        stale_charger = Charger.objects.create(
            charger_id="LOCK-1",
            last_heartbeat=now - timedelta(minutes=10),
        )

        original_lock = status_resets.store.SESSION_LOCK
        try:
            with patch.object(status_resets.store, "SESSION_LOCK", lock_path):
                with patch.object(status_resets.store, "stop_session_lock") as stop_lock:
                    status_resets.clear_stale_cached_statuses(max_age=timedelta(minutes=5))
                    stop_lock.assert_called_once()
        finally:
            status_resets.store.SESSION_LOCK = original_lock

        stale_charger.refresh_from_db()
        assert stale_charger.last_heartbeat == now - timedelta(minutes=10)
