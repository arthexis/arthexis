import requests
from urllib.parse import urljoin
from accounts.models import RFIDSource
from .background_reader import get_next_tag, start, stop
from .irq_wiring_check import check_irq_pin


def _test_remote(src):
    """Attempt to contact a remote RFID source and report status."""
    try:
        src.test_fetch(src.proxy_url)
    except Exception as exc:  # pragma: no cover - network issues
        return {"source": src.name, "error": str(exc)}
    return {"source": src.name, "status": "ok"}


def scan_sources():
    """Try each configured scanner in order and return the first result."""
    for src in RFIDSource.objects.order_by("default_order"):
        if src.proxy_url:
            try:
                url = src._build_url(src.proxy_url)
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                return resp.json()
            except Exception:
                continue
        else:
            result = get_next_tag()
            if result and result.get("rfid"):
                if src.uuid:
                    result.setdefault("source", str(src.uuid))
                return result
    return {"rfid": None, "label_id": None}


def restart_sources():
    """Restart scanners in order until one succeeds."""
    for src in RFIDSource.objects.order_by("default_order"):
        if src.proxy_url:
            try:
                url = urljoin(src._build_url(src.proxy_url), "restart/")
                resp = requests.post(url, timeout=5)
                resp.raise_for_status()
                return resp.json()
            except Exception:
                continue
        else:
            try:
                stop()
                start()
                test = get_next_tag()
                if test is not None and not test.get("error"):
                    return {"status": "restarted"}
            except Exception:
                pass
    return {"error": "no scanner available"}


def test_sources():
    """Check local and remote RFID sources for availability."""

    local_result = None
    remote_results = []
    for src in RFIDSource.objects.order_by("default_order"):
        if src.proxy_url:
            remote_results.append(_test_remote(src))
        elif local_result is None:
            local_result = check_irq_pin()
    if local_result is None:
        local_result = {"error": "no scanner detected"}
    return {"local": local_result, "remote": remote_results}
