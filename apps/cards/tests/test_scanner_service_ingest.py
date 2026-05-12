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


@pytest.mark.django_db
def test_ingest_service_scans_recovers_when_log_rotates(monkeypatch, settings, tmp_path):
    settings.BASE_DIR = str(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOG_DIR", str(log_dir), raising=False)

    log_file = log_dir / "rfid-scans.ndjson"
    first_payload = {"rfid": "ABCD1234", "service_mode": "service"}
    second_payload = {"rfid": "FEDC4321", "service_mode": "service"}

    log_file.write_text(json.dumps(first_payload) + "\n", encoding="utf-8")
    first_processed = ingest_service_scans()

    assert first_processed == 1
    assert RFIDAttempt.objects.filter(rfid="ABCD1234").count() == 1

    rotated_log_file = log_dir / "rfid-scans.rotated.ndjson"
    rotated_log_file.write_text(json.dumps(second_payload) + "\n", encoding="utf-8")
    rotated_log_file.replace(log_file)
    second_processed = ingest_service_scans()

    assert second_processed == 1
    assert RFIDAttempt.objects.filter(rfid="FEDC4321").count() == 1
    assert RFIDAttempt.objects.count() == 2


@pytest.mark.django_db
def test_ingest_service_scans_honors_legacy_integer_offset(
    monkeypatch, settings, tmp_path
):
    settings.BASE_DIR = str(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOG_DIR", str(log_dir), raising=False)

    log_file = log_dir / "rfid-scans.ndjson"
    first_payload = {"rfid": "ABCD1234", "service_mode": "service"}
    second_payload = {"rfid": "FEDC4321", "service_mode": "service"}
    first_line = json.dumps(first_payload) + "\n"
    second_line = json.dumps(second_payload) + "\n"
    log_file.write_text(first_line + second_line, encoding="utf-8")

    offset_path = tmp_path / ".locks" / "rfid-scan.offset"
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(str(len(first_line)), encoding="utf-8")

    processed = ingest_service_scans()

    assert processed == 1
    assert RFIDAttempt.objects.count() == 1
    assert RFIDAttempt.objects.filter(rfid="ABCD1234").count() == 0
    assert RFIDAttempt.objects.filter(rfid="FEDC4321").count() == 1


@pytest.mark.django_db
def test_ingest_service_scans_ignores_stale_label_id(
    monkeypatch, settings, tmp_path
):
    settings.BASE_DIR = str(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOG_DIR", str(log_dir), raising=False)

    log_file = log_dir / "rfid-scans.ndjson"
    payload = {"rfid": "ABCD1234", "label_id": 999, "service_mode": "service"}
    log_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    processed = ingest_service_scans()

    assert processed == 1
    attempt = RFIDAttempt.objects.get()
    assert attempt.rfid == "ABCD1234"
    assert attempt.label_id is None
    assert attempt.payload["label_id"] == 999


@pytest.mark.django_db
@pytest.mark.parametrize("label_id", ["", 0, "0", "not-int"])
def test_ingest_service_scans_normalizes_invalid_label_id(
    monkeypatch, settings, tmp_path, label_id
):
    settings.BASE_DIR = str(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOG_DIR", str(log_dir), raising=False)

    log_file = log_dir / "rfid-scans.ndjson"
    payload = {
        "rfid": "ABCD1234",
        "label_id": label_id,
        "service_mode": "service",
    }
    log_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    processed = ingest_service_scans()

    assert processed == 1
    attempt = RFIDAttempt.objects.get()
    assert attempt.rfid == "ABCD1234"
    assert attempt.label_id is None
    assert attempt.payload["label_id"] == label_id
