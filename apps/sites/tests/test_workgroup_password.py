import datetime as dt
import io
import json
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import connection
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import reverse

from apps.links.models import Reference
from apps.sites.workgroup_passwords import current_password, password_for_date


def test_workgroup_password_is_stable_for_day_and_changes_next_day():
    day = dt.date(2026, 5, 16)

    password = password_for_date(day, seed="test-seed")

    assert password == password_for_date(day, seed="test-seed")
    assert password != password_for_date(day + dt.timedelta(days=1), seed="test-seed")
    assert password.count("-") == 1
    assert all(part.isalpha() for part in password.split("-"))


@override_settings(
    WORKGROUP_DAILY_PASSWORD_SEED="view-seed",
    WORKGROUP_DAILY_PASSWORD_TIMEZONE="America/Monterrey",
)
def test_current_workgroup_password_uses_configured_timezone():
    password = current_password(dt.datetime(2026, 5, 16, 5, 30, tzinfo=dt.UTC))

    assert password.date == dt.date(2026, 5, 15)
    assert password.timezone_name == "America/Monterrey"
    assert password.password == password_for_date(dt.date(2026, 5, 15), seed="view-seed")


@pytest.mark.django_db
@override_settings(
    WORKGROUP_DAILY_PASSWORD_SEED="view-seed",
    WORKGROUP_DAILY_PASSWORD_TIMEZONE="America/Monterrey",
)
def test_workgroup_page_shows_daily_password_and_usage(client):
    expected = current_password().password

    response = client.get(reverse("pages:workgroup"))

    assert response.status_code == 200
    assert "The Workgroup" in response.content.decode()
    assert expected in response.content.decode()
    assert "ssh -p 2222 play@arthexis.com" in response.content.decode()
    assert "connect &lt;account&gt; &lt;account-password&gt;" in response.content.decode()


def test_footer_renders_workgroup_as_same_window_internal_link():
    html = render_to_string(
        "core/footer.html",
        {
            "footer_refs": [
                SimpleNamespace(value="/workgroup/", alt_text="The Workgroup")
            ],
            "show_footer": True,
            "show_release": False,
        },
    )

    assert 'href="/workgroup/"' in html
    assert 'target="_blank"' not in html
    assert "The Workgroup" in html


def test_footer_renders_protocol_relative_urls_as_external_links():
    html = render_to_string(
        "core/footer.html",
        {
            "footer_refs": [
                SimpleNamespace(value="//example.com/workgroup", alt_text="External")
            ],
            "show_footer": True,
            "show_release": False,
        },
    )

    assert 'href="//example.com/workgroup"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


@pytest.mark.django_db
def test_workgroup_footer_migration_prefers_active_reference():
    migration = import_module(
        "apps.links.migrations.0008_replace_wizards_with_workgroup_footer"
    )
    deleted_canonical = Reference.all_objects.create(
        alt_text="The Workgroup",
        value="/workgroup/",
        method="link",
        include_in_footer=True,
        footer_visibility="public",
        is_seed_data=True,
    )
    Reference.all_objects.filter(pk=deleted_canonical.pk).update(is_deleted=True)
    active_wizard = Reference.objects.create(
        alt_text="The Wizards",
        value="https://company.wizards.com/",
        method="link",
        include_in_footer=True,
        footer_visibility="public",
        is_seed_data=True,
    )

    migration.replace_wizards_with_workgroup_footer(
        django_apps,
        SimpleNamespace(connection=connection),
    )

    active_wizard.refresh_from_db()
    assert active_wizard.alt_text == "The Workgroup"
    assert active_wizard.value == "/workgroup/"
    assert active_wizard.method == "link"
    assert active_wizard.include_in_footer is True
    assert active_wizard.footer_visibility == "public"
    assert active_wizard.is_seed_data is True
    assert active_wizard.is_deleted is False
    assert Reference.objects.filter(
        alt_text="The Workgroup",
        value="/workgroup/",
    ).count() == 1


@override_settings(
    WORKGROUP_DAILY_PASSWORD_SEED="command-seed",
    WORKGROUP_DAILY_PASSWORD_TIMEZONE="America/Monterrey",
)
def test_workgroup_password_command_prints_json_for_date():
    stdout = io.StringIO()

    call_command("workgroup_password", "--date", "2026-05-16", "--json", stdout=stdout)

    output = json.loads(stdout.getvalue())
    assert output["date"] == "2026-05-16"
    assert output["timezone"] == "America/Monterrey"
    assert output["password"] == password_for_date(dt.date(2026, 5, 16), seed="command-seed")


@override_settings(WORKGROUP_DAILY_PASSWORD_SEED="command-seed")
def test_workgroup_password_command_apply_user_does_not_print_password(monkeypatch):
    calls = []

    def fake_run(command, *, input, text, check, capture_output):
        calls.append(
            {
                "command": command,
                "input": input,
                "text": text,
                "check": check,
                "capture_output": capture_output,
            }
        )

    monkeypatch.setattr(
        "apps.sites.management.commands.workgroup_password.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "apps.sites.management.commands.workgroup_password.os.geteuid",
        lambda: 0,
        raising=False,
    )
    stdout = io.StringIO()

    call_command(
        "workgroup_password",
        "--date",
        "2026-05-16",
        "--apply-user",
        "play",
        stdout=stdout,
    )

    generated = password_for_date(dt.date(2026, 5, 16), seed="command-seed")
    assert calls == [
        {
            "command": ["chpasswd"],
            "input": f"play:{generated}\n",
            "text": True,
            "check": True,
            "capture_output": True,
        }
    ]
    assert generated not in stdout.getvalue()
    assert "Updated password for play for 2026-05-16." in stdout.getvalue()
