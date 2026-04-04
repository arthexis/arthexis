from __future__ import annotations

import json

import pytest

from scripts.startup_orchestration import extract_payload


def test_extract_payload_reads_last_json_object() -> None:
    output = "\n".join(
        (
            "channel_layer.redis_url_invalid",
            "channel_layer.fallback_inmemory",
            "Version: v0.2.3 r92795c",
            '{"status":"ok","launch":{"celery_embedded":true}}',
        )
    )

    payload = extract_payload(output)

    assert payload["status"] == "ok"
    assert payload["launch"]["celery_embedded"] is True


def test_extract_payload_reads_pretty_printed_json_object() -> None:
    output = """{
  "status": "ok",
  "launch": {
    "celery_embedded": true
  }
}"""

    payload = extract_payload(output)

    assert payload["status"] == "ok"
    assert payload["launch"]["celery_embedded"] is True


def test_extract_payload_raises_when_no_json_object_present() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_payload("Version: v0.2.3 r92795c\nstartup failed\n")
