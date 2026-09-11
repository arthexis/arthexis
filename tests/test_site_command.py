from types import SimpleNamespace

import pytest
from django.contrib.sites.models import Site
from django.core.management import call_command


@pytest.fixture(autouse=True)
def _disable_gway_forwarding(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.core.management.commands.site.shutil.which",
        lambda command: None,
    )


@pytest.mark.django_db
def test_site_without_arguments_reports_current_configuration(capsys) -> None:
    site = Site.objects.get_current()
    site.domain = "current.example.com"
    site.name = "Current"
    site.save()
    Site.objects.clear_cache()

    call_command("site")

    output = capsys.readouterr().out
    assert "domain: current.example.com" in output
    assert "name: Current" in output
    assert "web:" in output
    assert "host: 127.0.0.1" in output
    assert "port: 8888" in output


@pytest.mark.django_db
def test_site_updates_domain_name_and_refreshes_local_node(monkeypatch, capsys) -> None:
    refreshed: list[bool] = []

    def fake_call_command(name, **kwargs):
        assert name == "ensure_local_node"
        refreshed.append(True)

    monkeypatch.setattr(
        "apps.core.management.commands.site.call_command",
        fake_call_command,
    )

    call_command("site", "HTTPS://Charge.Example.com/", name="Charging Site")

    site = Site.objects.get_current()
    assert site.domain == "charge.example.com"
    assert site.name == "Charging Site"
    assert refreshed == [True]
    assert "domain: charge.example.com" in capsys.readouterr().out


@pytest.mark.django_db
def test_site_can_skip_node_refresh_for_lifecycle_use(monkeypatch) -> None:
    def unexpected(*args, **kwargs):
        raise AssertionError("node refresh should be skipped")

    monkeypatch.setattr(
        "apps.core.management.commands.site.call_command",
        unexpected,
    )

    call_command("site", "example.org", no_refresh_node=True)

    assert Site.objects.get_current().domain == "example.org"


@pytest.mark.django_db
def test_site_forwards_enriched_site_to_gway(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        "apps.core.management.commands.site.shutil.which",
        lambda command: "/usr/local/bin/gway",
    )

    def fake_run(arguments, **kwargs):
        calls.append((list(arguments), kwargs))
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    monkeypatch.setattr(
        "apps.core.management.commands.site.subprocess.run",
        fake_run,
    )

    site = Site.objects.get_current()
    site.domain = "charge.example.com"
    site.name = "arthexis"
    site.save()
    Site.objects.clear_cache()

    call_command("site", no_refresh_node=True)

    assert calls[0][0] == [
        "/usr/local/bin/gway",
        "web",
        "site",
        "arthexis",
    ]
    assert calls[0][1]["check"] is False
    assert calls[1] == (
        [
            "/usr/local/bin/gway",
            "web",
            "site",
            "arthexis",
            "--create",
            "--domain",
            "charge.example.com",
            "--host",
            "127.0.0.1",
            "--port",
            "8888",
            "--health-path",
            "/health/",
        ],
        {"check": True, "text": True},
    )
