from __future__ import annotations

from types import SimpleNamespace

from apps.nodes import tasks


def test_active_interface_label_shortens_wlan(monkeypatch):
    monkeypatch.setattr(
        tasks.psutil,
        "net_if_stats",
        lambda: {"wlan1": SimpleNamespace(isup=True)},
    )

    assert tasks._active_interface_label() == "wln1"
