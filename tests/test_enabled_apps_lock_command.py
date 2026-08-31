"""Tests for the enabled_apps_lock management command."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.app.models import Application
from utils.enabled_apps_lock import (
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
)


def test_enabled_apps_lock_command_writes_json_explained_lock(tmp_path):
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Terminal",
        "--include",
        "apps.ocpp",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_path = tmp_path / ".locks" / "enabled_apps.lck"
    lock_entries = set(lock_path.read_text(encoding="utf-8").splitlines())
    apps_by_selector = {
        item["selector"]: item["reasons"] for item in payload["enabledApps"]
    }

    assert payload["roleProfile"] == "terminal"
    assert payload["written"] is True
    assert payload["fallbackReason"] is None
    assert payload["lockPath"] == str(lock_path)
    assert "apps.ocpp" in lock_entries
    assert "explicit-include" in apps_by_selector["apps.ocpp"]
    assert "role-default:terminal" in apps_by_selector["apps.imager"]
    assert "all-node" in apps_by_selector["apps.core"]
    assert (
        "separate explicit destructive operator action" in payload["destructiveCleanup"]
    )
    assert "apps.ocpp" in read_enabled_apps_lock_direct_entries(tmp_path)
    assert "apps.ocpp" not in read_enabled_apps_lock_direct_sources(tmp_path)


def test_enabled_apps_lock_command_marks_explicit_route_include_direct(tmp_path):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Terminal",
        "--include",
        "apps.ocpp",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    direct_sources = read_enabled_apps_lock_direct_sources(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in direct_entries
    assert "apps.ocpp" not in direct_sources


def test_enabled_apps_lock_command_does_not_mark_all_node_ocpp_direct(tmp_path):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Terminal",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in lock_entries
    assert "apps.imager" in direct_entries
    assert "apps.ocpp" not in direct_entries


def test_enabled_apps_lock_command_does_not_mark_control_ocpp_direct(tmp_path):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Control",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in lock_entries
    assert "apps.ocpp" not in direct_entries


def test_enabled_apps_lock_command_marks_hosted_ocpp_direct(tmp_path):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Watchtower",
        "--feature-pack",
        "hosted-ocpp",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    direct_sources = read_enabled_apps_lock_direct_sources(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in direct_entries
    assert direct_sources["apps.ocpp"] == "feature-pack:hosted_ocpp"


def test_enabled_apps_lock_command_marks_explicit_ocpp_include_direct(tmp_path):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Terminal",
        "--include",
        "apps.ocpp",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in direct_entries


def test_enabled_apps_lock_command_public_commerce_keeps_public_apps_indirect(
    tmp_path,
):
    call_command(
        "enabled_apps_lock",
        "--role",
        "Watchtower",
        "--feature-pack",
        "public-commerce",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--strict",
    )

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "apps.shop" not in direct_entries


def test_enabled_apps_lock_command_unknown_role_uses_full_fallback():
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "SetupRecovery",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    apps_by_selector = {
        item["selector"]: item["reasons"] for item in payload["enabledApps"]
    }

    assert payload["roleProfile"] is None
    assert payload["fallbackReason"] == "unknown role profile: SetupRecovery"
    assert "apps.ocpp" in apps_by_selector
    assert apps_by_selector["apps.ocpp"] == ["full-app-fallback:unknown-role"]
    assert payload["written"] is False


def test_enabled_apps_lock_command_unknown_role_writes_fallback_direct_metadata(
    tmp_path,
):
    call_command(
        "enabled_apps_lock",
        "--role",
        "SetupRecovery",
        "--write",
        "--base-dir",
        str(tmp_path),
    )

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "apps.ocpp" in direct_entries


def test_enabled_apps_lock_command_unknown_role_rejects_unknown_feature_pack():
    with pytest.raises(CommandError, match="Unknown feature pack"):
        call_command(
            "enabled_apps_lock",
            "--role",
            "SetupRecovery",
            "--feature-pack",
            "noep",
        )


def test_enabled_apps_lock_command_strict_unknown_role_fails():
    with pytest.raises(CommandError, match="Unknown role profile"):
        call_command("enabled_apps_lock", "--role", "SetupRecovery", "--strict")


def test_enabled_apps_lock_command_rejects_unknown_bare_include():
    with pytest.raises(CommandError, match="Unknown --include app selector"):
        call_command("enabled_apps_lock", "--include", "classificaton")


def test_enabled_apps_lock_command_rejects_unknown_dotted_include():
    with pytest.raises(CommandError, match="Unknown --include app selector"):
        call_command("enabled_apps_lock", "--include", "apps.classificaton")


def test_enabled_apps_lock_command_rejects_unknown_bare_disable():
    with pytest.raises(CommandError, match="Unknown --disable app selector"):
        call_command("enabled_apps_lock", "--disable", "classificaton")


def test_enabled_apps_lock_command_rejects_unknown_dotted_disable():
    with pytest.raises(CommandError, match="Unknown --disable app selector"):
        call_command("enabled_apps_lock", "--disable", "apps.classificaton")


def test_enabled_apps_lock_command_rejects_core_disable():
    with pytest.raises(CommandError, match="apps.core cannot be disabled"):
        call_command("enabled_apps_lock", "--disable", "core")


def test_enabled_apps_lock_command_rejects_disables_that_prune_required_apps(tmp_path):
    with pytest.raises(CommandError, match="apps.core cannot be omitted"):
        call_command(
            "enabled_apps_lock",
            "--disable",
            "discovery",
            "--write",
            "--base-dir",
            str(tmp_path),
        )

    assert not (tmp_path / ".locks" / "enabled_apps.lck").exists()


def test_enabled_apps_lock_command_rejects_cli_baseline_disable():
    with pytest.raises(CommandError, match="Baseline --disable app selector"):
        call_command("enabled_apps_lock", "--disable", "django.contrib.admin")


def test_enabled_apps_lock_command_allows_env_baseline_disable(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_DISABLED_APPS", raising=False)
    monkeypatch.setenv("ARTHEXIS_ROLE_APP_DISABLED_APPS", "django.contrib.admin")
    stdout = StringIO()

    call_command("enabled_apps_lock", "--role", "Terminal", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    enabled_selectors = {item["selector"] for item in payload["enabledApps"]}

    assert payload["disabledApps"] == ["django.contrib.admin"]
    assert "django.contrib.admin" not in enabled_selectors


@pytest.mark.django_db
def test_enabled_apps_lock_command_preserves_application_disables(tmp_path):
    Application.objects.create(name="repos", enabled=False)
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Control",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        "--strict",
        "--preserve-application-disables",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert payload["roleProfile"] == "control"
    assert payload["preservedApplicationDisabledApps"] == ["apps.repos"]
    assert "apps.repos" not in lock_entries


@pytest.mark.django_db
def test_enabled_apps_lock_command_ignores_deleted_application_disables(tmp_path):
    Application.all_objects.create(name="repos", enabled=False, is_deleted=True)
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Control",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        "--strict",
        "--preserve-application-disables",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert payload["preservedApplicationDisabledApps"] == []


@pytest.mark.django_db
def test_enabled_apps_lock_command_preserves_optional_application_disables(
    tmp_path,
):
    Application.objects.create(name="repos", enabled=False)
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Control",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        "--strict",
        "--preserve-application-disables",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert payload["preservedApplicationDisabledApps"] == ["apps.repos"]
    assert "apps.cards" in lock_entries
    assert "apps.repos" not in lock_entries


@pytest.mark.django_db
def test_enabled_apps_lock_command_ignores_non_route_required_dependency_disables(
    tmp_path,
):
    Application.objects.create(name="maps", enabled=False)
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Control",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        "--strict",
        "--preserve-application-disables",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert payload["preservedApplicationDisabledApps"] == []
    assert "apps.maps" in lock_entries


@pytest.mark.django_db
def test_enabled_apps_lock_command_prunes_preserved_dependency_disables(tmp_path):
    Application.objects.create(name="unusedapp", enabled=False)
    stdout = StringIO()

    call_command(
        "enabled_apps_lock",
        "--role",
        "Watchtower",
        "--write",
        "--base-dir",
        str(tmp_path),
        "--json",
        "--strict",
        "--preserve-application-disables",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    lock_entries = set(
        (tmp_path / ".locks" / "enabled_apps.lck")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert payload["preservedApplicationDisabledApps"] == []
    assert "apps.ops" in lock_entries


@pytest.mark.django_db
def test_enabled_apps_lock_command_rejects_disabled_application_app(tmp_path):
    Application.objects.create(name="app", enabled=False)

    with pytest.raises(CommandError, match="apps.app cannot be disabled"):
        call_command(
            "enabled_apps_lock",
            "--role",
            "Control",
            "--write",
            "--base-dir",
            str(tmp_path),
            "--strict",
            "--preserve-application-disables",
        )

    assert not (tmp_path / ".locks" / "enabled_apps.lck").exists()
