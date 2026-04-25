from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ap_portal_server.py"


def load_portal_module():
    spec = importlib.util.spec_from_file_location("ap_portal_server", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_config(module, tmp_path):
    state_dir = tmp_path / "ap_portal"
    return module.PortalConfig(
        bind="127.0.0.1",
        port=0,
        assets_dir=tmp_path,
        state_dir=state_dir,
        authorized_macs_path=state_dir / "authorized_macs.txt",
        consents_path=state_dir / "consents.jsonl",
        activity_path=state_dir / "activity.jsonl",
        source_url="https://github.com/arthexis/arthexis/blob/main/scripts/ap_portal_server.py",
        sync_firewall=False,
    )


def test_monitoring_notice_is_explicit_and_points_to_source():
    module = load_portal_module()

    assert "ARE being monitored" in module.MONITORING_NOTICE
    assert module.DEFAULT_SOURCE_URL.startswith("https://github.com/arthexis/arthexis")
    assert module.DEFAULT_SOURCE_URL.endswith("/scripts/ap_portal_server.py")


def test_subscribe_records_consent_activity_and_authorizes_client(tmp_path):
    module = load_portal_module()
    state = module.PortalState(make_config(module, tmp_path))
    state.resolve_mac = lambda _ip: "aa:bb:cc:dd:ee:ff"

    result = state.subscribe(
        email="Guest@Example.COM",
        accept_terms=True,
        ip_address="10.42.0.25",
        user_agent="client-test",
        host="arthexis.net",
    )

    assert result["authorized"] is True
    assert result["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert "ARE being monitored" in result["monitoring_notice"]
    assert state.config.authorized_macs_path.read_text(encoding="utf-8") == "aa:bb:cc:dd:ee:ff\n"

    consent = json.loads(state.config.consents_path.read_text(encoding="utf-8").splitlines()[0])
    assert consent["email"] == "guest@example.com"
    assert consent["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert consent["source_code_url"].startswith("https://github.com/arthexis/arthexis")

    activity = [json.loads(line) for line in state.config.activity_path.read_text(encoding="utf-8").splitlines()]
    assert activity[-1]["event_type"] == "consent_accepted"
    assert activity[-1]["ip_address"] == "10.42.0.25"


def test_status_records_activity_and_exposes_monitoring_paths(tmp_path):
    module = load_portal_module()
    state = module.PortalState(make_config(module, tmp_path))
    state.resolve_mac = lambda _ip: "aa:bb:cc:dd:ee:ff"

    payload = state.status_for_request(
        ip_address="10.42.0.25",
        user_agent="client-test",
        path="/api/status",
        host="arthexis.net",
    )

    assert payload["authorized"] is False
    assert payload["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert "activity.jsonl" in payload["activity_recording"]["activity_log"]

    activity = json.loads(state.config.activity_path.read_text(encoding="utf-8").splitlines()[0])
    assert activity["event_type"] == "status_check"
    assert activity["monitoring_notice"] == module.MONITORING_NOTICE


def test_client_summary_combines_authorization_consent_and_activity(tmp_path):
    module = load_portal_module()
    state = module.PortalState(make_config(module, tmp_path))
    state.resolve_mac = lambda _ip: "aa:bb:cc:dd:ee:ff"
    state.subscribe(
        email="guest@example.com",
        accept_terms=True,
        ip_address="10.42.0.25",
        user_agent="client-test",
        host="arthexis.net",
    )
    state.record_request(
        ip_address="10.42.0.25",
        user_agent="client-test",
        method="GET",
        path="/",
        host="arthexis.net",
        referer="",
    )

    summary = state.activity.client_summary()

    assert summary[0]["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert summary[0]["authorized"] is True
    assert summary[0]["email"] == "guest@example.com"
    assert summary[0]["event_count"] >= 1


def test_firewall_ruleset_keeps_authorized_clients_and_redirects_unapproved_http():
    module = load_portal_module()
    ruleset = module.FirewallManager(interface="wlan0")._render_ruleset(["aa:bb:cc:dd:ee:ff"])

    assert "table inet arthexis_ap_portal" in ruleset
    assert "elements = { aa:bb:cc:dd:ee:ff }" in ruleset
    assert "meta l4proto tcp redirect to :80" in ruleset
    assert 'iifname "wlan0" drop' in ruleset
