import logging

from django.utils import timezone

from apps.cards.models import RFID as CoreRFID, RFIDAttempt
from apps.energy.models import CustomerAccount
from channels.db import database_sync_to_async

from ...models import Charger, Transaction
from ... import store

logger = logging.getLogger(__name__)


class AuthMixin:
    async def _get_account(self, id_tag: str) -> CustomerAccount | None:
        """Return the customer account for the provided RFID if valid."""
        if not id_tag:
            return None

        def _resolve() -> CustomerAccount | None:
            matches = CoreRFID.matching_queryset(id_tag).filter(allowed=True)
            if not matches.exists():
                return None
            return (
                CustomerAccount.objects.filter(rfids__in=matches)
                .distinct()
                .first()
            )

        return await database_sync_to_async(_resolve)()

    async def _ensure_rfid_seen(self, id_tag: str) -> CoreRFID | None:
        """Ensure an RFID record exists and update its last seen timestamp."""

        if not id_tag:
            return None

        normalized = id_tag.upper()

        def _ensure() -> CoreRFID:
            now = timezone.now()
            tag, _created = CoreRFID.register_scan(normalized)
            updates = []
            if not tag.allowed:
                tag.allowed = True
                updates.append("allowed")
            if not tag.released:
                tag.released = True
                updates.append("released")
            if tag.last_seen_on != now:
                tag.last_seen_on = now
                updates.append("last_seen_on")
            if updates:
                tag.save(update_fields=sorted(set(updates)))
            return tag

        return await database_sync_to_async(_ensure)()

    def _log_unlinked_rfid(self, rfid: str) -> None:
        """Record a warning when an RFID is authorized without an account."""

        message = (
            f"Authorized RFID {rfid} on charger {self.charger_id} without linked customer account"
        )
        logger.warning(message)
        store.add_log(
            store.pending_key(self.charger_id),
            message,
            log_type="charger",
        )

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

    async def _update_local_authorization_state(self, version: int | None) -> None:
        """Persist the reported local authorization list version."""

        timestamp = timezone.now()

        def _apply() -> None:
            updates: dict[str, object] = {"local_auth_list_updated_at": timestamp}
            if version is not None:
                updates["local_auth_list_version"] = int(version)

            targets: list = []
            if self.charger and getattr(self.charger, "pk", None):
                targets.append(self.charger)
            aggregate = self.aggregate_charger
            if (
                aggregate
                and getattr(aggregate, "pk", None)
                and not any(target.pk == aggregate.pk for target in targets if target.pk)
            ):
                targets.append(aggregate)

            if not targets:
                return

            for target in targets:
                Charger.objects.filter(pk=target.pk).update(**updates)
                for field, value in updates.items():
                    setattr(target, field, value)

        await database_sync_to_async(_apply)()

    async def _apply_local_authorization_entries(
        self, entries: list[dict[str, object]]
    ) -> int:
        """Create or update RFID records from a local authorization list."""

        def _apply() -> int:
            processed = 0
            now = timezone.now()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                id_tag = entry.get("idTag")
                id_tag_text = str(id_tag or "").strip().upper()
                if not id_tag_text:
                    continue
                info = entry.get("idTagInfo")
                status_value = ""
                if isinstance(info, dict):
                    status_value = str(info.get("status") or "").strip()
                status_key = status_value.lower()
                allowed_flag = status_key in {"", "accepted", "concurrenttx"}
                defaults = {"allowed": allowed_flag, "released": allowed_flag}
                tag, _ = CoreRFID.update_or_create_from_code(id_tag_text, defaults)
                updates: set[str] = set()
                if tag.allowed != allowed_flag:
                    tag.allowed = allowed_flag
                    updates.add("allowed")
                if tag.released != allowed_flag:
                    tag.released = allowed_flag
                    updates.add("released")
                if tag.last_seen_on != now:
                    tag.last_seen_on = now
                    updates.add("last_seen_on")
                if updates:
                    tag.save(update_fields=sorted(updates))
                processed += 1
            return processed

        return await database_sync_to_async(_apply)()
