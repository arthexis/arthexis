"""Call error handlers for network profile actions."""
from __future__ import annotations

import json

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CPNetworkProfileDeployment
from ..types import CallErrorContext


async def handle_set_network_profile_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        deployment = CPNetworkProfileDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        detail_text = (description or "").strip()
        if not detail_text and details:
            try:
                detail_text = json.dumps(details, sort_keys=True)
            except Exception:
                detail_text = str(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        deployment.mark_status("Error", detail_text, response=details)
        deployment.completed_at = timezone.now()
        deployment.save(update_fields=["completed_at", "updated_at"])

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True
