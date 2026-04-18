from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PORTAL_SERVER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "ap_portal_server.py"
APP_JS_PATH = Path(__file__).resolve().parents[3] / "config" / "data" / "ap_portal" / "app.js"
INDEX_HTML_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "data" / "ap_portal" / "index.html"
)

SPEC = importlib.util.spec_from_file_location("ap_portal_server", PORTAL_SERVER_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE.__name__] = MODULE
SPEC.loader.exec_module(MODULE)


def _make_config(tmp_path: Path, *, redirect_url: str | None = None):
    return MODULE.PortalConfig(
        bind="127.0.0.1",
        port=9080,
        assets_dir=tmp_path,
        state_dir=tmp_path,
        authorized_macs_path=tmp_path / "authorized_macs.txt",
        consents_path=tmp_path / "consents.jsonl",
        redirect_url=redirect_url,
    )


def test_portal_state_stays_local_by_default(tmp_path, monkeypatch):
    firewall_syncs: list[set[str]] = []

    class FakeFirewallManager:
        def __init__(self, interface: str = "wlan0") -> None:
            self.interface = interface

        def sync(self, macs: set[str]) -> None:
            firewall_syncs.append(set(macs))

    monkeypatch.setattr(MODULE, "FirewallManager", FakeFirewallManager)
    state = MODULE.PortalState(_make_config(tmp_path))
    monkeypatch.setattr(state, "resolve_mac", lambda _ip: "aa:bb:cc:dd:ee:ff")

    subscribe_payload = state.subscribe(
        email="user@example.com",
        existing_user="",
        accept_terms=True,
        ip_address="10.42.0.20",
        user_agent="pytest",
    )
    status_payload = state.status_for_ip("10.42.0.20")

    assert "redirect_url" not in subscribe_payload
    assert "redirect_url" not in status_payload
    assert subscribe_payload["authorized"] is True
    assert status_payload["authorized"] is True
    assert firewall_syncs == [set(), {"aa:bb:cc:dd:ee:ff"}]


def test_portal_state_redirects_only_when_explicitly_configured(tmp_path, monkeypatch):
    redirect_url = "http://10.42.0.1:8000/"

    class FakeFirewallManager:
        def __init__(self, interface: str = "wlan0") -> None:
            self.interface = interface

        def sync(self, macs: set[str]) -> None:
            del macs

    monkeypatch.setattr(MODULE, "FirewallManager", FakeFirewallManager)
    state = MODULE.PortalState(_make_config(tmp_path, redirect_url=redirect_url))
    monkeypatch.setattr(state, "resolve_mac", lambda _ip: "aa:bb:cc:dd:ee:ff")

    subscribe_payload = state.subscribe(
        email="user@example.com",
        existing_user="",
        accept_terms=True,
        ip_address="10.42.0.20",
        user_agent="pytest",
    )
    status_payload = state.status_for_ip("10.42.0.20")

    assert subscribe_payload["redirect_url"] == redirect_url
    assert status_payload["redirect_url"] == redirect_url


def test_portal_state_existing_user_handoff_tracks_suite_username(tmp_path, monkeypatch):
    firewall_syncs: list[set[str]] = []

    class FakeFirewallManager:
        def __init__(self, interface: str = "wlan0") -> None:
            self.interface = interface

        def sync(self, macs: set[str]) -> None:
            firewall_syncs.append(set(macs))

    monkeypatch.setattr(MODULE, "FirewallManager", FakeFirewallManager)
    state = MODULE.PortalState(_make_config(tmp_path))
    monkeypatch.setattr(state, "resolve_mac", lambda _ip: "aa:bb:cc:dd:ee:11")
    monkeypatch.setattr(
        state,
        "_lookup_existing_suite_user",
        lambda _identifier: "suite-user",
    )

    subscribe_payload = state.subscribe(
        email="",
        existing_user="suite-user",
        accept_terms=True,
        ip_address="10.42.0.21",
        user_agent="pytest",
    )
    status_payload = state.status_for_ip("10.42.0.21")

    assert subscribe_payload["login_mode"] == "suite_user"
    assert subscribe_payload["suite_username"] == "suite-user"
    assert status_payload["login_mode"] == "suite_user"
    assert status_payload["suite_username"] == "suite-user"
    assert firewall_syncs == [set(), {"aa:bb:cc:dd:ee:11"}]


def test_portal_frontend_has_no_hardcoded_external_redirect():
    script = APP_JS_PATH.read_text(encoding="utf-8")
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")

    assert "neverssl.com" not in script
    assert "handleAuthorizedFlow(payload" in script
    assert "payload.redirect_url" in script
    assert "scheduleRedirect(data.redirect_url, 1400)" in script
    assert "suiteLoginUrl" in script
    assert "payload.suite_username" in script
    assert "localArthexisUrl" in script
    assert "https://google.com/" in script
    assert "Choose where to go next." in html
    assert 'id="google-link"' in html
    assert 'id="local-arthexis-link"' in html
    assert 'id="existing-user"' in html
    assert "Existing Arthexis user" in html
    assert "same login" in html
