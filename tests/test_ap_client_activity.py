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
