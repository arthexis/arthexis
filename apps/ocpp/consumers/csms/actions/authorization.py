"""Authorization action handler for CSMS consumer."""

from __future__ import annotations

from channels.db import database_sync_to_async

from apps.cards.models import RFID as CoreRFID


class AuthorizationActionHandler:
    """Handle Authorize requests with a small async contract."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer

    async def handle(self, payload, _msg_id, _raw, _text_data) -> dict:
        id_tag = payload.get("idTag")
        account = await self.consumer._get_account(id_tag)
        status = "Invalid"

        if self.consumer.charger.require_rfid:
            energy_accounts_enabled = await self.consumer._energy_accounts_enabled()
            credits_required = await self.consumer._energy_credits_required()
            tag = None
            tag_created = False
            if id_tag:
                tag, tag_created = await database_sync_to_async(CoreRFID.register_scan)(
                    id_tag
                )

            if account:
                account_authorized = (
                    energy_accounts_enabled and not credits_required
                ) or await database_sync_to_async(account.can_authorize)()
                if account_authorized:
                    status = "Accepted"
            elif (
                not energy_accounts_enabled
                and id_tag
                and tag
                and not tag_created
                and tag.allowed
            ):
                status = "Accepted"
                self.consumer._log_unlinked_rfid(tag.rfid)
        else:
            await self.consumer._ensure_rfid_seen(id_tag)
            status = "Accepted"

        return {"idTagInfo": {"status": status}}
