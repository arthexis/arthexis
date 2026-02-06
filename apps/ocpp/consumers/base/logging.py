import json
from datetime import datetime

from ... import store


class LoggingMixin:
    def _log_ocpp201_notification(self, label: str, payload) -> None:
        message = label
        if payload:
            try:
                payload_text = json.dumps(payload, separators=(",", ":"))
            except (TypeError, ValueError):
                payload_text = str(payload)
            if payload_text and payload_text != "{}":
                message += f": {payload_text}"
        store.add_log(self.store_key, message, log_type="charger")

    def _log_notify_monitoring_report(
        self,
        *,
        request_id: int | None,
        seq_no: int | None,
        generated_at: datetime | None,
        tbc: bool,
        items: int,
    ) -> None:
        details: list[str] = []
        if request_id is not None:
            details.append(f"requestId={request_id}")
        if seq_no is not None:
            details.append(f"seqNo={seq_no}")
        if generated_at is not None:
            details.append(f"generatedAt={generated_at.isoformat()}")
        details.append(f"tbc={tbc}")
        details.append(f"items={items}")
        message = "NotifyMonitoringReport"
        if details:
            message += f": {', '.join(details)}"
        store.add_log(self.store_key, message, log_type="charger")
