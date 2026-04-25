from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.library.models import (
    KindleLibraryTransfer,
    OwnerLibraryHolding,
    PublicLibraryEntry,
    RegisteredKindle,
)


class LibraryModelTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="kindle-owner")
        self.entry = PublicLibraryEntry.objects.create(
            source=PublicLibraryEntry.Source.KINDLE_STORE,
            external_id="B000000001",
            title="Tracked Book",
            creators="Ada Example",
            asin="B000000001",
            remote_version="remote-v1",
        )

    def test_registered_kindle_is_tracked_separately_from_library_holding(self):
        kindle = RegisteredKindle.objects.create(
            name="Travel Kindle",
            serial_number="G000TEST123",
            owner=self.owner,
            kindle_identifier="kindle-device-id",
        )
        holding = OwnerLibraryHolding.objects.create(owner=self.owner, entry=self.entry)

        transfer = KindleLibraryTransfer.objects.create(
            registered_kindle=kindle,
            holding=holding,
            operation=KindleLibraryTransfer.Operation.COPY,
            direction=KindleLibraryTransfer.Direction.LIBRARY_TO_DEVICE,
            status=KindleLibraryTransfer.Status.SUCCEEDED,
        )

        self.assertEqual(kindle.library_transfers.get(), transfer)
        self.assertEqual(holding.transfers.get(), transfer)

    def test_holding_reconciliation_detects_newer_local_copy(self):
        now = timezone.now()
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            local_path="/library/Tracked Book.kfx",
            local_version="local-v2",
            local_modified_at=now,
            remote_version="remote-v1",
            remote_modified_at=now - timedelta(hours=1),
            backup_path="/backup/Tracked Book.kfx",
            backup_version="local-v1",
        )

        state = holding.refresh_reconciliation_state()

        self.assertTrue(holding.local_is_newer())
        self.assertFalse(holding.remote_is_newer())
        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.LOCAL_NEWER)
        self.assertIsNotNone(holding.reconciled_at)

    def test_holding_reconciliation_prefers_newer_timestamp_for_divergent_checksums(
        self,
    ):
        now = timezone.now()
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            local_path="/library/Tracked Book.kfx",
            local_modified_at=now,
            local_checksum_sha256="a" * 64,
            remote_version="remote-v1",
            remote_modified_at=now - timedelta(hours=1),
            remote_checksum_sha256="b" * 64,
        )

        state = holding.refresh_reconciliation_state()

        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.LOCAL_NEWER)

    def test_holding_reconciliation_prefers_missing_local_over_remote_timestamp(self):
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            remote_version="remote-v1",
            remote_modified_at=timezone.now(),
        )

        state = holding.refresh_reconciliation_state()

        self.assertTrue(holding.remote_is_newer())
        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.MISSING_LOCAL)

    def test_holding_reconciliation_prefers_missing_remote_over_local_timestamp(self):
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            local_path="/library/Tracked Book.kfx",
            local_modified_at=timezone.now(),
        )

        state = holding.refresh_reconciliation_state()

        self.assertTrue(holding.local_is_newer())
        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.MISSING_REMOTE)

    @override_settings(USE_TZ=False)
    def test_holding_reconciliation_timestamp_handles_disabled_timezones(self):
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            local_path="/library/Tracked Book.kfx",
            local_modified_at=timezone.now(),
            remote_version="remote-v1",
        )

        state = holding.refresh_reconciliation_state()

        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.LOCAL_NEWER)
        self.assertIsNotNone(holding.reconciled_at)

    def test_holding_reconciliation_detects_matching_checksums(self):
        holding = OwnerLibraryHolding.objects.create(
            owner=self.owner,
            entry=self.entry,
            local_path="/library/Tracked Book.kfx",
            remote_version="remote-v1",
            local_checksum_sha256="a" * 64,
            remote_checksum_sha256="a" * 64,
        )

        state = holding.refresh_reconciliation_state(save=True)

        holding.refresh_from_db()
        self.assertEqual(state, OwnerLibraryHolding.ReconciliationState.MATCHED)
        self.assertEqual(
            holding.reconciliation_state,
            OwnerLibraryHolding.ReconciliationState.MATCHED,
        )

    def test_transfer_duration_is_available_for_copy_outcomes(self):
        started_at = timezone.now()
        finished_at = started_at + timedelta(seconds=30)
        holding = OwnerLibraryHolding.objects.create(owner=self.owner, entry=self.entry)
        transfer = KindleLibraryTransfer.objects.create(
            holding=holding,
            operation=KindleLibraryTransfer.Operation.BACKUP,
            status=KindleLibraryTransfer.Status.SUCCEEDED,
            bytes_copied=2048,
            started_at=started_at,
            finished_at=finished_at,
            reconciliation_state=OwnerLibraryHolding.ReconciliationState.MATCHED,
            metadata={"postbox": "out-of-scope"},
        )

        self.assertEqual(transfer.duration, timedelta(seconds=30))
        self.assertEqual(transfer.metadata["postbox"], "out-of-scope")
