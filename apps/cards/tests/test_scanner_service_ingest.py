from __future__ import annotations

import json

import pytest

from apps.cards.models import RFIDAttempt
from apps.cards.scanner import ingest_service_scans


@pytest.mark.django_db
def test_ingest_service_scans_reads_ndjson_log(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = str(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOG_DIR", str(log_dir), raising=False)

    log_file = log_dir / "rfid-scans.ndjson"
    payload = {"rfid": "ABCD1234", "service_mode": "service"}
    log_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    processed = ingest_service_scans()

    assert processed == 1
    attempt = RFIDAttempt.objects.get()
    assert attempt.source == RFIDAttempt.Source.SERVICE
    assert attempt.rfid == "ABCD1234"

    processed_again = ingest_service_scans()

    assert processed_again == 0
    assert RFIDAttempt.objects.count() == 1
