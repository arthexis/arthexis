"""RFID/account lookup helpers for OCPP consumer transaction auth flows."""

import logging

from channels.db import database_sync_to_async
from django.utils import timezone

from apps.cards.models import RFID as CoreRFID, RFIDAttempt
from apps.energy.models import CustomerAccount

from ... import store
from ...models import Transaction

logger = logging.getLogger(__name__)


class RfidMixin:
    """Provide RFID/account helper operations reused across transaction handlers."""

    async def _get_account(self, id_tag: str) -> CustomerAccount | None:
        """Return the customer account for the provided RFID if valid."""
        if not id_tag:
            return None

        def _resolve() -> CustomerAccount | None:
            matches = CoreRFID.matching_queryset(id_tag).filter(allowed=True)
            return CustomerAccount.objects.filter(rfids__in=matches).distinct().first()

        return await database_sync_to_async(_resolve)()

    async def _ensure_rfid_seen(self, id_tag: str, tag: CoreRFID | None = None) -> CoreRFID | None:
        """Ensure an RFID exists, auto-approve/release it, and refresh its `last_seen_on` timestamp."""
        if not id_tag:
            return None

        normalized = id_tag.upper()

        def _ensure() -> CoreRFID:
            now = timezone.now()
            current_tag = tag
            if current_tag is None:
                current_tag, _created = CoreRFID.register_scan(normalized)
            updates = []
            if not current_tag.allowed:
                current_tag.allowed = True
                updates.append("allowed")
            if not current_tag.released:
                current_tag.released = True
                updates.append("released")
            current_tag.last_seen_on = now
            updates.append("last_seen_on")
            if updates:
                current_tag.save(update_fields=sorted(set(updates)))
            return current_tag

        return await database_sync_to_async(_ensure)()

    def _log_unlinked_rfid(self, rfid: str) -> None:
        """Record a warning when an RFID is authorized without an account."""
        masked_rfid = rfid[-4:].rjust(len(rfid), "*") if len(rfid) > 4 else "****"
        message = (
            f"Authorized RFID {masked_rfid} on charger {self.charger_id} without linked customer account"
        )
        logger.warning(message)
        store.add_log(store.pending_key(self.charger_id), message, log_type="charger")

    async def _record_rfid_attempt(
        self,
        *,
        rfid: str,
        status: RFIDAttempt.Status,
        account: CustomerAccount | None,
        transaction: Transaction | None = None,
    ) -> None:
        """Persist RFID session attempt metadata for reporting."""
        normalized = (rfid or "").strip().upper()
        if not normalized:
            return

        charger = self.charger

        def _create_attempt() -> None:
            RFIDAttempt.record_attempt(
                payload={"rfid": normalized},
                source=RFIDAttempt.Source.OCPP,
                status=status,
                charger_id=charger.pk,
                account_id=account.pk if account else None,
                transaction_id=transaction.pk if transaction else None,
            )

        await database_sync_to_async(_create_attempt)()
