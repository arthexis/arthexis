import requests
from urllib.parse import urljoin
from accounts.models import RFIDSource
from .background_reader import get_next_tag, start, stop


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
