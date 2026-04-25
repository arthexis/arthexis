from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


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


def test_accept_terms_requires_explicit_true():
    module = load_portal_module()

    assert module._accept_terms_is_explicit(True) is True
    assert module._accept_terms_is_explicit(False) is False
    assert module._accept_terms_is_explicit("true") is False
    assert module._accept_terms_is_explicit("false") is False
    assert module._accept_terms_is_explicit("0") is False
    assert module._accept_terms_is_explicit(1) is False


def test_subscribe_rejects_string_false_consent_without_authorizing(tmp_path):
    module = load_portal_module()
    state = module.PortalState(make_config(module, tmp_path))
    state.resolve_mac = lambda _ip: "aa:bb:cc:dd:ee:ff"

    with pytest.raises(ValueError, match="accept the access terms"):
        state.subscribe(
            email="guest@example.com",
            accept_terms=module._accept_terms_is_explicit("false"),
            ip_address="10.42.0.25",
            user_agent="client-test",
            host="arthexis.net",
        )

    assert not state.config.authorized_macs_path.exists()
    assert not state.config.consents_path.exists()


def test_subscribe_does_not_persist_authorization_when_firewall_sync_fails(tmp_path):
    module = load_portal_module()
    config = make_config(module, tmp_path)
    state = module.PortalState(config)
    config = module.PortalConfig(
        bind=config.bind,
        port=config.port,
        assets_dir=config.assets_dir,
        state_dir=config.state_dir,
        authorized_macs_path=config.authorized_macs_path,
        consents_path=config.consents_path,
        activity_path=config.activity_path,
        source_url=config.source_url,
        sync_firewall=True,
    )
    state.config = config
    state.resolve_mac = lambda _ip: "aa:bb:cc:dd:ee:ff"

    def fail_sync(_macs):
        raise module.FirewallSyncError("nft failed")

    state._firewall.sync = fail_sync

    with pytest.raises(module.FirewallSyncError, match="nft failed"):
        state.subscribe(
            email="guest@example.com",
            accept_terms=True,
            ip_address="10.42.0.25",
            user_agent="client-test",
            host="arthexis.net",
        )

    assert not state.config.authorized_macs_path.exists()
    assert not state.config.consents_path.exists()
    assert "aa:bb:cc:dd:ee:ff" not in state._authorized


def test_client_ip_prefers_nginx_real_ip_over_spoofed_forwarded_for():
    module = load_portal_module()
    headers = {
        "X-Forwarded-For": "203.0.113.50, 198.51.100.10",
        "X-Real-IP": "10.42.0.25",
    }

    assert module._client_ip_from_headers(headers, "127.0.0.1") == "10.42.0.25"


def test_client_ip_uses_trusted_rightmost_forwarded_hop_without_real_ip():
    module = load_portal_module()
    headers = {"X-Forwarded-For": "203.0.113.50, 10.42.0.25"}

    assert module._client_ip_from_headers(headers, "127.0.0.1") == "10.42.0.25"


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


def test_firewall_sync_replaces_existing_table_in_single_apply(monkeypatch):
    module = load_portal_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.FirewallManager(interface="wlan0").sync({"aa:bb:cc:dd:ee:ff"})

    apply_calls = [call for call in calls if call[0] == ["nft", "-f", "-"]]
    assert len(apply_calls) == 1
    ruleset = apply_calls[0][1]["input"]
    assert ruleset.startswith("delete table inet arthexis_ap_portal\n")
    assert "elements = { aa:bb:cc:dd:ee:ff }" in ruleset


def test_resolve_mac_reads_proc_arp_without_subprocess(tmp_path, monkeypatch):
    module = load_portal_module()
    arp_table = tmp_path / "arp"
    arp_table.write_text(
        "\n".join(
            [
                "IP address       HW type     Flags       HW address            Mask     Device",
                "10.42.0.25       0x1         0x2         AA:BB:CC:DD:EE:FF     *        wlan0",
            ]
        ),
        encoding="utf-8",
    )

    def fail_run(*_args, **_kwargs):
        raise AssertionError("resolve_mac should not spawn neighbor lookup subprocesses")

    monkeypatch.setattr(module, "ARP_TABLE_PATH", arp_table)
    monkeypatch.setattr(module.subprocess, "run", fail_run)
    state = module.PortalState(make_config(module, tmp_path))

    assert state.resolve_mac("10.42.0.25") == "aa:bb:cc:dd:ee:ff"
    assert state.resolve_mac("10.42.0.26") is None


def test_read_limited_request_body_rejects_large_payload():
    module = load_portal_module()
    headers = {"Content-Length": str(module.MAX_PAYLOAD_BYTES + 1)}

    with pytest.raises(ValueError, match="Payload too large"):
        module._read_limited_request_body(headers, io.BytesIO())


def test_read_limited_request_body_rejects_negative_length_before_reading():
    module = load_portal_module()
    headers = {"Content-Length": "-1"}

    with pytest.raises(ValueError, match="Invalid Content-Length"):
        module._read_limited_request_body(headers, io.BytesIO(b"email=guest@example.com"))
