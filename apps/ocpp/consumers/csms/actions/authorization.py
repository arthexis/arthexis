"""Authorization action handler for CSMS consumer."""

from __future__ import annotations

from channels.db import database_sync_to_async

from apps.cards.models import RFID as CoreRFID, RFIDAttempt


class AuthorizationActionHandler:
    """Handle Authorize requests with a small async contract."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, _msg_id, _raw, _text_data) -> dict:
        id_tag = payload.get("idTag")
        account = await self.consumer._get_account(id_tag)
        tag = None
        tag_created = False
        if id_tag:
            tag, tag_created = await database_sync_to_async(CoreRFID.register_scan)(id_tag)

        decision = await self.consumer._evaluate_authorization_policy(
            id_tag=id_tag,
            account=account,
            tag=tag,
            tag_created=tag_created,
        )

        if id_tag and decision.should_mark_seen:
            tag = await self.consumer._ensure_rfid_seen(
                id_tag,
                tag=tag,
                auto_enroll=decision.should_auto_enroll,
            )

        if decision.log_unlinked_rfid and tag:
            self.consumer._log_unlinked_rfid(
                tag.rfid,
                reason=decision.reason,
                policy=decision.policy,
            )

        await self.consumer._record_rfid_attempt(
            rfid=id_tag or "",
            status=(
                RFIDAttempt.Status.ACCEPTED
                if decision.status == "Accepted"
                else RFIDAttempt.Status.REJECTED
            ),
            account=account,
            policy=decision.policy,
            reason=decision.reason,
        )

        return {
            "idTagInfo": {
                "status": decision.status,
            }
        }
