from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ap_client_activity.py"

def load_activity_module():
    spec = importlib.util.spec_from_file_location("ap_client_activity", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

def test_load_jsonl_limit_uses_bounded_tail_without_reading_whole_file(tmp_path, monkeypatch):
    module = load_activity_module()
    log_path = tmp_path / "activity.jsonl"
    rows = [
        {"event_type": "old"},
        {"event_type": "middle"},
        {"event_type": "new"},
    ]
    log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def fail_read_text(*_args, **_kwargs):
        raise AssertionError("_load_jsonl should not read the whole file for limited loads")

    monkeypatch.setattr(module.Path, "read_text", fail_read_text)

    loaded = module._load_jsonl(log_path, limit=2)

    assert [row["event_type"] for row in loaded] == ["middle", "new"]

def test_build_report_does_not_treat_consent_history_as_authorization(tmp_path):
    module = load_activity_module()
    state_dir = tmp_path / "ap_portal"
    state_dir.mkdir()
    (state_dir / "authorized_macs.txt").write_text("", encoding="utf-8")
    consent = {
        "accepted_at": "2026-04-25T09:00:00+00:00",
        "email": "guest@example.com",
        "ip_address": "10.42.0.25",
        "mac_address": "aa:bb:cc:dd:ee:ff",
    }
    (state_dir / "consents.jsonl").write_text(json.dumps(consent) + "\n", encoding="utf-8")

    report = module.build_report(state_dir, limit=500)

    assert report["authorized_client_count"] == 0
    assert report["clients"][0]["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert report["clients"][0]["email"] == "guest@example.com"
    assert report["clients"][0]["authorized"] is False
