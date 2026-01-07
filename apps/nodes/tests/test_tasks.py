from __future__ import annotations

from types import SimpleNamespace

from apps.nodes import tasks


def test_active_interface_label_prefers_up_wlan(monkeypatch):
    monkeypatch.setattr(tasks.uptime_utils.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        tasks.uptime_utils.psutil,
        "net_if_stats",
        lambda: {"wlan1": SimpleNamespace(isup=True)},
    )

    assert tasks._active_interface_label() == "wlan1"
