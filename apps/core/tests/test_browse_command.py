from __future__ import annotations

import io

import pytest
from django.contrib.sites.models import Site
from django.core.management import call_command

from apps.core.management.commands import browse as browse_command


@pytest.mark.django_db
def test_browse_command_opens_default_browser_when_ui_available(monkeypatch, settings):
    Site.objects.update_or_create(
        id=settings.SITE_ID, defaults={"domain": "example.test", "name": "Example"}
    )
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browse_command.sys, "platform", "linux")

    opened = {}

    def _fake_open(url, new=0, autoraise=True):
        opened["url"] = url
        opened["new"] = new
        return True

    monkeypatch.setattr(browse_command.webbrowser, "open", _fake_open)

    def _unexpected_run(*args, **kwargs):
        raise AssertionError("lynx should not be invoked when UI is available.")

    monkeypatch.setattr(browse_command.subprocess, "run", _unexpected_run)

    stdout = io.StringIO()
    call_command("browse", stdout=stdout)

    assert opened == {"url": "http://example.test", "new": 2}
    assert "Opening http://example.test in the default browser" in stdout.getvalue()


@pytest.mark.django_db
def test_browse_command_uses_lynx_without_ui(monkeypatch, settings):
    Site.objects.update_or_create(
        id=settings.SITE_ID, defaults={"domain": "example.test", "name": "Example"}
    )
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browse_command.sys, "platform", "linux")
    monkeypatch.setattr(browse_command.shutil, "which", lambda _: "/usr/bin/lynx")

    def _unexpected_open(*args, **kwargs):
        raise AssertionError("Default browser should not be invoked without UI.")

    monkeypatch.setattr(browse_command.webbrowser, "open", _unexpected_open)

    calls = {}

    def _fake_run(args, check=False):
        calls["args"] = args
        calls["check"] = check

        from types import SimpleNamespace
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(browse_command.subprocess, "run", _fake_run)

    stdout = io.StringIO()
    call_command("browse", stdout=stdout)

    assert calls == {"args": ["lynx", "http://example.test"], "check": False}
    assert "Opening http://example.test in lynx" in stdout.getvalue()
