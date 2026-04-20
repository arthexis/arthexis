import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt

from .irq_wiring_check import check_irq_pin
from .utils import convert_endianness_value, normalize_endianness
from .rfid_service import deep_read_via_service, request_service, rfid_scan_log_path


RECENT_SCAN_WINDOW_SECONDS = float(
    os.environ.get("RFID_SCAN_RECENT_WINDOW_SECONDS", "10")
)
SCANNER_SOURCES = {
    RFIDAttempt.Source.SERVICE,
    RFIDAttempt.Source.BROWSER,
    RFIDAttempt.Source.CAMERA,
    RFIDAttempt.Source.ON_DEMAND,
}
SCAN_INGEST_OFFSET_FILE = "rfid-scan.offset"
SERVICE_SCAN_DB_ERROR = "scan requests are handled via the database"


def _normalize_scan_response(
    result: dict, *, endianness: str | None = None, service_mode: str
) -> dict:
    response = dict(result)
    response["service_mode"] = service_mode
    stored_endianness = normalize_endianness(response.get("endianness"))
    response["endianness"] = stored_endianness
    requested_endianness = (
        normalize_endianness(endianness)
        if endianness is not None
        else stored_endianness
    )

    rfid_value = response.get("rfid")
    if rfid_value:
        normalized_value = str(rfid_value).upper()
        if requested_endianness != stored_endianness:
            converted = convert_endianness_value(
                normalized_value,
                from_endianness=stored_endianness,
                to_endianness=requested_endianness,
            )
            response["rfid"] = converted
            response["endianness"] = requested_endianness
            if response.get("created") and response.get("label_id"):
                tag = RFID.objects.filter(pk=response["label_id"]).first()
                if tag:
                    tag.rfid = converted
                    tag.endianness = requested_endianness
                    try:
                        tag.save(update_fields=["rfid", "endianness"])
                    except IntegrityError:
                        response["rfid"] = normalized_value
                        response["endianness"] = stored_endianness
        else:
            response["rfid"] = normalized_value
    else:
        response["rfid"] = None

    return response


def _service_mode_for_source(source: str) -> str:
    return "service" if source == RFIDAttempt.Source.SERVICE else "on-demand"


def build_attempt_response(
    attempt: RFIDAttempt, *, endianness: str | None = None
) -> dict:
    payload = dict(attempt.payload or {})
    payload.setdefault("rfid", attempt.rfid)
    if attempt.label_id:
        payload.setdefault("label_id", attempt.label_id)
    if attempt.allowed is not None:
        payload.setdefault("allowed", attempt.allowed)
    service_mode = payload.get("service_mode") or _service_mode_for_source(
        attempt.source
    )
    normalized = _normalize_scan_response(
        payload, endianness=endianness, service_mode=service_mode
    )
    normalized["attempt_id"] = attempt.pk
    return normalized


def poll_scan_attempt(
    *,
    after_id: int | None = None,
    endianness: str | None = None,
    sources: set[str] | None = None,
) -> dict:
    ingest_service_scans()
    sources = sources or SCANNER_SOURCES
    queryset = RFIDAttempt.objects.filter(source__in=sources)
    if after_id:
        attempt = queryset.filter(pk__gt=after_id).order_by("pk").first()
        if attempt:
            return build_attempt_response(attempt, endianness=endianness)
        latest_id = queryset.order_by("-pk").values_list("pk", flat=True).first()
        return {"rfid": None, "service_mode": "service", "last_id": latest_id}
    latest = queryset.order_by("-pk").first()
    if latest:
        age_seconds = (timezone.now() - latest.attempted_at).total_seconds()
        if age_seconds <= RECENT_SCAN_WINDOW_SECONDS:
            return build_attempt_response(latest, endianness=endianness)
    latest_id = latest.pk if latest else None
    return {"rfid": None, "service_mode": "service", "last_id": latest_id}


def record_scan_attempt(
    result: dict,
    *,
    source: str,
    status: str | None = None,
    authenticated: bool | None = None,
) -> RFIDAttempt | None:
    return RFIDAttempt.record_attempt(
        result,
        source=source,
        status=status,
        authenticated=authenticated,
    )


def _scan_ingest_offset_path() -> Path:
    return Path(settings.BASE_DIR) / ".locks" / SCAN_INGEST_OFFSET_FILE


def _read_ingest_offset_state(offset_path: Path) -> tuple[int | None, int]:
    """Return the last ingested inode and byte offset for the scan log."""

    try:
        raw_value = offset_path.read_text(encoding="utf-8").strip()
    except Exception:
        return None, 0
    if not raw_value:
        return None, 0
    try:
        payload = json.loads(raw_value)
    except (TypeError, ValueError):
        try:
            return None, int(raw_value)
        except Exception:
            return None, 0
    if not isinstance(payload, dict):
        try:
            return None, int(payload)
        except (TypeError, ValueError):
            return None, 0
    inode = payload.get("inode")
    offset = payload.get("offset")
    try:
        normalized_inode = int(inode) if inode is not None else None
    except Exception:
        normalized_inode = None
    try:
        normalized_offset = int(offset)
    except Exception:
        normalized_offset = 0
    return normalized_inode, normalized_offset


def _write_ingest_offset_state(offset_path: Path, inode: int | None, offset: int) -> None:
    """Persist the inode and byte offset for the next scan-log ingest run."""

    payload = {"inode": inode, "offset": max(0, int(offset))}
    offset_path.write_text(json.dumps(payload), encoding="utf-8")


def ingest_service_scans() -> int:
    """Ingest scanner service NDJSON entries into RFIDAttempt history."""

    log_path = rfid_scan_log_path()
    if not log_path.exists():
        return 0
    offset_path = _scan_ingest_offset_path()
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    last_inode, last_offset = _read_ingest_offset_state(offset_path)

    processed = 0
    with log_path.open("r", encoding="utf-8") as scan_log:
        stat = os.fstat(scan_log.fileno())
        current_inode = getattr(stat, "st_ino", None)
        file_size = stat.st_size
        if last_inode is not None and current_inode is not None and last_inode != current_inode:
            last_offset = 0
        if last_offset < 0 or last_offset > file_size:
            last_offset = 0
        scan_log.seek(last_offset)
        while True:
            line = scan_log.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = dict(json.loads(stripped))
            except (TypeError, ValueError):
                continue
            payload.setdefault("service_mode", "service")
            attempt = RFIDAttempt.record_attempt(
                payload,
                source=RFIDAttempt.Source.SERVICE,
                status=RFIDAttempt.Status.SCANNED,
            )
            if attempt is not None:
                processed += 1
        _write_ingest_offset_state(offset_path, current_inode, scan_log.tell())
    return processed


def scan_sources(
    request=None,
    *,
    endianness: str | None = None,
    timeout: float | None = None,
    no_irq: bool = False,
):
    """Read the next RFID tag from the scanner service."""
    timeout_value = timeout if timeout is not None else 0.5
    result = request_service("scan", payload={"timeout": timeout_value}, timeout=timeout_value + 0.2)
    if result is None:
        return {"error": "scanner service unavailable", "service_mode": "service"}
    if not result:
        return {"rfid": None, "label_id": None, "service_mode": "service"}
    if result.get("error"):
        if SERVICE_SCAN_DB_ERROR in str(result.get("error", "")).lower():
            return _scan_from_attempts(timeout=timeout_value, endianness=endianness)
        result["service_mode"] = "service"
        return result

    normalized = _normalize_scan_response(
        result, endianness=endianness, service_mode="service"
    )
    return normalized


def _scan_from_attempts(*, timeout: float, endianness: str | None) -> dict:
    latest_id = (
        RFIDAttempt.objects.filter(source=RFIDAttempt.Source.SERVICE)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first()
        or 0
    )
    start = time.monotonic()
    while time.monotonic() - start < max(timeout, 0):
        ingest_service_scans()
        attempt = (
            RFIDAttempt.objects.filter(
                source=RFIDAttempt.Source.SERVICE,
                pk__gt=latest_id,
            )
            .order_by("pk")
            .first()
        )
        if attempt is not None:
            return build_attempt_response(attempt, endianness=endianness)
        time.sleep(0.1)
    return {"rfid": None, "label_id": None, "service_mode": "service"}


def restart_sources():
    """Scanner service restart is managed by the service manager."""
    return {"status": "managed-by-service"}


def test_sources():
    """Check scanner availability through IRQ and service probes."""
    service_ping = request_service("ping", timeout=0.3)
    irq = check_irq_pin()
    if service_ping and service_ping.get("status") == "ok":
        return {"status": "ok", "service_mode": "service", "irq": irq}
    return {"error": "no scanner service available", "irq": irq}


def enable_deep_read_mode(duration: float = 60) -> dict:
    """Toggle the RFID reader deep read mode and report the new state."""
    del duration
    response = deep_read_via_service()
    if response is not None:
        response.setdefault("service_mode", "service")
        return response
    return {"error": "no scanner service available", "service_mode": "service"}
