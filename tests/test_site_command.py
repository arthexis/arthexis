import pytest
from django.contrib.sites.models import Site
from django.core.management import call_command


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
    assert "port: 8000" in output


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
