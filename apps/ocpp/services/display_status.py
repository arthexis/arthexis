"""Display-ready read-only projection of OCPP charger health."""

from apps.ocpp.services.timeline_status import query_timeline_status


def query_display_status(identity: str) -> dict[str, object]:
    """Return a compact observer payload suitable for local displays."""
    snapshot = query_timeline_status(identity)
    return {
        "charger": snapshot["charger_identity"],
        "condition": snapshot["condition"],
        "state": snapshot["state"],
        "pending_work": snapshot["pending_work"],
        "oldest_pending_age_seconds": snapshot["oldest_pending_age_seconds"],
        "processing_rate_per_minute": snapshot["processing_rate_per_minute"],
        "max_processing_latency_seconds": snapshot["max_processing_latency_seconds"],
        "recent_request_errors": snapshot["recent_request_errors"],
        "recent_outbound_errors": snapshot["recent_outbound_errors"],
        "recent_retry_attempts": snapshot["recent_retry_attempts"],
        "last_authorization_age_seconds": snapshot["last_authorization_age_seconds"],
        "last_authorization_status": snapshot["last_authorization_status"],
        "connection_live": snapshot["connection_live"],
        "as_of": snapshot["as_of"],
    }
