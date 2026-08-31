from __future__ import annotations

import shlex
from dataclasses import replace
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands import health as health_command
from apps.core.services import health_reporting
from apps.core.services.health import HealthCheckDefinition


class DummyResponse:
    def __init__(self, data, status_code: int = 201):
        self._data = data
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self._data

    def close(self):
        self.closed = True


class DummyGitHubIssueClient:
    owner = "octo"
    repository = "demo"
    token = "token-1"


def _patch_health_issue_client(monkeypatch):
    from apps.repos.services import github as github_service

    monkeypatch.setattr(
        github_service.GitHubIssue,
        "from_active_repository",
        classmethod(lambda cls: DummyGitHubIssueClient()),
    )
    return github_service


def _positive_health_runner(*, stdout, style, **_options) -> None:
    stdout.write(style.SUCCESS("Positive runner executed."))


def _failing_health_runner(**_options) -> None:
    raise CommandError("Synthetic failed with token=secret-value")


def test_health_runs_control_lcd_target_without_screens_app_selector(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Control"
    settings.INSTALLED_APPS = ["apps.core"]
    monkeypatch.setitem(
        health_command.HEALTH_CHECKS,
        "core.lcd_service",
        replace(
            health_command.HEALTH_CHECKS["core.lcd_service"],
            runner=f"{__name__}._positive_health_runner",
        ),
    )
    stdout = StringIO()

    call_command("health", "--target", "core.lcd_service", stdout=stdout)

    output = stdout.getvalue()
    assert "[skipped]" not in output
    assert "Positive runner executed." in output
    assert "Health checks passed." in output


def test_health_reports_failed_target_when_requested(monkeypatch, settings) -> None:
    settings.INSTALLED_APPS = ["apps.core"]
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
        app_selector="apps.core",
    )
    monkeypatch.setitem(health_command.HEALTH_CHECKS, definition.target, definition)
    reports: list[dict[str, object]] = []

    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_failure",
        lambda **kwargs: reports.append(kwargs),
    )
    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_recovery",
        lambda **kwargs: pytest.fail("failing target must not be recovered"),
    )

    with pytest.raises(SystemExit) as exc_info:
        call_command("health", "--target", "core.synthetic", "--report-github")

    assert exc_info.value.code == 1
    assert reports[0]["definition"] == definition
    assert reports[0]["failure_message"] == "Synthetic failed with token=secret-value"
    assert (
        reports[0]["command_text"]
        == "manage.py health --target core.synthetic --report-github"
    )


def test_health_report_command_includes_target_specific_flags(
    monkeypatch, settings
) -> None:
    settings.INSTALLED_APPS = ["apps.core"]
    definition = HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=f"{__name__}._failing_health_runner",
        app_selector="apps.core",
    )
    monkeypatch.setitem(health_command.HEALTH_CHECKS, definition.target, definition)
    reports: list[dict[str, object]] = []

    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_failure",
        lambda **kwargs: reports.append(kwargs),
    )

    with pytest.raises(SystemExit) as exc_info:
        call_command(
            "health",
            "--target",
            "core.rfid",
            "--rfid-value",
            "123",
            "--rfid-kind",
            "badge",
            "--rfid-pretty",
            "--report-github",
        )

    assert exc_info.value.code == 1
    assert reports[0]["command_text"] == (
        "manage.py health --target core.rfid --rfid-value 123 "
        "--rfid-kind badge --rfid-pretty --report-github"
    )


def test_health_report_commands_include_only_relevant_target_flags(
    monkeypatch, settings
) -> None:
    settings.INSTALLED_APPS = ["apps.core"]
    admin_definition = HealthCheckDefinition(
        target="core.admin",
        group="core",
        description="Verify default admin account health",
        runner=f"{__name__}._failing_health_runner",
        app_selector="apps.core",
    )
    rfid_definition = HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=f"{__name__}._failing_health_runner",
        app_selector="apps.core",
    )
    monkeypatch.setitem(
        health_command.HEALTH_CHECKS,
        admin_definition.target,
        admin_definition,
    )
    monkeypatch.setitem(
        health_command.HEALTH_CHECKS,
        rfid_definition.target,
        rfid_definition,
    )
    reports: list[dict[str, object]] = []

    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_failure",
        lambda **kwargs: reports.append(kwargs),
    )

    with pytest.raises(SystemExit) as exc_info:
        call_command(
            "health",
            "--target",
            "core.admin",
            "--target",
            "core.rfid",
            "--rfid-value",
            "123",
            "--report-github",
        )

    assert exc_info.value.code == 1
    report_commands = {
        report["definition"].target: report["command_text"] for report in reports
    }
    assert report_commands == {
        "core.admin": "manage.py health --target core.admin --report-github",
        "core.rfid": (
            "manage.py health --target core.rfid --rfid-value 123 --report-github"
        ),
    }


def test_health_reports_recovered_target_when_requested(monkeypatch, settings) -> None:
    settings.INSTALLED_APPS = ["apps.core"]
    definition = HealthCheckDefinition(
        target="core.positive_runner",
        group="core",
        description="Exercise recovery reporting",
        runner=f"{__name__}._positive_health_runner",
        app_selector="apps.core",
    )
    monkeypatch.setitem(health_command.HEALTH_CHECKS, definition.target, definition)
    recoveries: list[dict[str, object]] = []

    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_failure",
        lambda **kwargs: pytest.fail("passing target must not report a failure"),
    )
    monkeypatch.setattr(
        "apps.core.services.health.report_health_check_recovery",
        lambda **kwargs: recoveries.append(kwargs),
    )

    call_command("health", "--target", definition.target, "--report-github")

    assert recoveries == [
        {
            "definition": definition,
            "command_text": (
                "manage.py health --target core.positive_runner --report-github"
            ),
        }
    ]


def test_health_command_text_includes_optional_health_flags() -> None:
    command_text = health_command._health_command_text(
        {
            "target": ["core.lcd_send"],
            "group": ["release"],
            "force": True,
            "release": "1.2.3",
            "rfid_value": "04 AB",
            "rfid_kind": "MIFARE",
            "rfid_pretty": True,
            "lcd_subject": "Power warning",
            "lcd_body": "Needs reset",
            "lcd_expires_at": "2026-06-12T12:00:00Z",
            "lcd_sticky": True,
            "lcd_channel_type": "i2c",
            "lcd_channel_num": "1",
            "lcd_timeout": 3.5,
            "lcd_poll_interval": 0.1,
            "lcd_confirmed": True,
            "report_github": True,
        }
    )

    assert command_text == (
        "manage.py health --target core.lcd_send --group release --force "
        "--release 1.2.3 --rfid-value '04 AB' --rfid-kind MIFARE "
        "--rfid-pretty --lcd-subject 'Power warning' --lcd-body 'Needs reset' "
        "--lcd-expires-at 2026-06-12T12:00:00Z --lcd-sticky "
        "--lcd-channel-type i2c --lcd-channel-num 1 --lcd-timeout 3.5 "
        "--lcd-poll-interval 0.1 --lcd-confirmed --report-github"
    )


def test_health_command_text_limits_force_to_supported_targets() -> None:
    options = {
        "force": True,
        "report_github": True,
    }

    assert health_command._health_command_text(
        options,
        definition=health_command.HEALTH_CHECKS["core.admin"],
    ) == "manage.py health --target core.admin --force --report-github"
    assert health_command._health_command_text(
        options,
        definition=health_command.HEALTH_CHECKS["core.time"],
    ) == "manage.py health --target core.time --report-github"


def test_health_command_text_preserves_option_looking_values() -> None:
    command_text = health_command._health_command_text(
        {
            "lcd_body": "-offline",
            "report_github": True,
        },
        definition=health_command.HEALTH_CHECKS["core.lcd_send"],
    )

    assert (
        command_text
        == "manage.py health --target core.lcd_send --lcd-body=-offline "
        "--report-github"
    )


def test_health_github_reporting_skips_when_feature_disabled(monkeypatch) -> None:
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: False)
    monkeypatch.setattr(
        health_reporting,
        "_create_failure_issue",
        lambda **kwargs: pytest.fail("disabled reporting should not create issues"),
    )

    assert (
        health_reporting.report_health_check_failure(
            definition=definition,
            failure_message="failed",
            command_text="manage.py health --target core.synthetic --report-github",
        )
        is None
    )


def test_health_github_reporting_redacts_sensitive_values() -> None:
    shell_escaped_command = shlex.join(
        [
            "manage.py",
            "health",
            "--target",
            "core.lcd_send",
            "--lcd-body",
            "token=foo' bar",
        ]
    )
    redacted = health_reporting._redact(
        "\tAuthorization: Bearer header-token\n"
        "authorization: plain-secret\r\n"
        "manage.py health --target demo --token=flag-secret "
        "password=password-secret api-key=api-key-secret "
        "api_key=api-key-secret-2 apikey=api-key-secret-3 "
        "secret=secret-value mytoken=visible "
        "rfid=rfid-assignment rfid_value=rfid-value-assignment "
        "rfid-value=rfid-dash-assignment "
        "token=visible-too token= spaced-too token = spaced-too-2 "
        "password='correct horse battery staple' "
        'secret="quoted secret value" '
        "--rfid-value DEADBEEF --rfid-value=FACEBEEF "
        "--rfid-value '04 AB' --lcd-body 'token=foo bar' "
        f"{shell_escaped_command}"
    )

    assert "\tAuthorization: [REDACTED]\n" in redacted
    assert "authorization: [REDACTED]\r\n" in redacted
    assert "--token=[REDACTED]" in redacted
    assert "password=[REDACTED]" in redacted
    assert "api-key=[REDACTED]" in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "apikey=[REDACTED]" in redacted
    assert "secret=[REDACTED]" in redacted
    assert "rfid=[REDACTED]" in redacted
    assert "rfid_value=[REDACTED]" in redacted
    assert "rfid-value=[REDACTED]" in redacted
    assert "password='[REDACTED]'" in redacted
    assert 'secret="[REDACTED]"' in redacted
    assert "--lcd-body 'token=[REDACTED]'" in redacted
    assert "--rfid-value [REDACTED]" in redacted
    assert "--rfid-value=[REDACTED]" in redacted
    assert "mytoken=visible" in redacted
    assert "header-token" not in redacted
    assert "plain-secret" not in redacted
    assert "flag-secret" not in redacted
    assert "password-secret" not in redacted
    assert "api-key-secret" not in redacted
    assert "secret-value" not in redacted
    assert "DEADBEEF" not in redacted
    assert "FACEBEEF" not in redacted
    assert "rfid-assignment" not in redacted
    assert "rfid-value-assignment" not in redacted
    assert "rfid-dash-assignment" not in redacted
    assert "visible-too" not in redacted
    assert "spaced-too" not in redacted
    assert "correct horse" not in redacted
    assert "quoted secret value" not in redacted
    assert "foo bar" not in redacted
    assert "bar" not in redacted


def test_health_fingerprint_is_scoped_to_node_identity(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"

    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")
    terminal_node_a = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-b")
    terminal_node_b = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    settings.NODE_ROLE = "Control"
    control_node_b = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )

    assert len({terminal_node_a, terminal_node_b, control_node_b}) == 3


def test_health_fingerprint_is_scoped_to_non_sensitive_command_options(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=f"{__name__}._failing_health_runner",
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")

    mifare_value = health_reporting.health_check_fingerprint(
        definition,
        command_text=(
            "manage.py health --target core.rfid --rfid-value 123 "
            "--rfid-kind MIFARE --report-github"
        ),
    )
    uid_value = health_reporting.health_check_fingerprint(
        definition,
        command_text=(
            "manage.py health --target core.rfid --rfid-value 123 "
            "--rfid-kind UID --report-github"
        ),
    )

    assert mifare_value != uid_value


def test_health_fingerprint_ignores_sensitive_rfid_values(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=f"{__name__}._failing_health_runner",
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")

    assert health_reporting.health_check_fingerprint(
        definition,
        command_text=(
            "manage.py health --target core.rfid --rfid-value 123 --report-github"
        ),
    ) == health_reporting.health_check_fingerprint(
        definition,
        command_text=(
            "manage.py health --target core.rfid --rfid-value=456 --report-github"
        ),
    )


def test_health_fingerprint_canonicalizes_equivalent_selectors(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.admin",
        group="core",
        description="Verify default admin account health",
        runner=f"{__name__}._positive_health_runner",
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")

    target_selector = health_reporting.health_check_fingerprint(
        definition,
        command_text="manage.py health --target core.admin --force --report-github",
    )
    group_selector = health_reporting.health_check_fingerprint(
        definition,
        command_text="manage.py health --group core --force --report-github",
    )
    all_selector = health_reporting.health_check_fingerprint(
        definition,
        command_text="manage.py health --all --force --report-github",
    )

    assert target_selector == group_selector == all_selector


def test_health_fingerprint_uses_raw_command_identity_for_secret_values(
    monkeypatch, settings
) -> None:
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.lcd_send",
        group="core",
        description="Send and validate an LCD lock-file message",
        runner=f"{__name__}._failing_health_runner",
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")
    first_command = "manage.py health --target core.lcd_send --lcd-body 'token=foo'"
    second_command = "manage.py health --target core.lcd_send --lcd-body 'token=bar'"

    assert health_reporting._redact(first_command) == health_reporting._redact(
        second_command
    )
    assert health_reporting.health_check_fingerprint(
        definition,
        command_text=first_command,
    ) != health_reporting.health_check_fingerprint(
        definition,
        command_text=second_command,
    )


def test_health_github_reporting_creates_labeled_issue(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path
    settings.NODE_ROLE = "Terminal"
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    calls: dict[str, object] = {}
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    def fake_create_issue(owner, repository, **kwargs):
        calls["owner"] = owner
        calls["repository"] = repository
        calls.update(kwargs)
        return DummyResponse({"html_url": "https://github.com/octo/demo/issues/21"})

    monkeypatch.setattr(github_service, "create_issue", fake_create_issue)

    issue_url = health_reporting.report_health_check_failure(
        definition=definition,
        failure_message="Synthetic failed with token=secret-value",
        command_text=command_text,
    )

    assert issue_url == "https://github.com/octo/demo/issues/21"
    assert calls["owner"] == "octo"
    assert calls["repository"] == "demo"
    assert calls["labels"] == ("automation", "bug", "priority: high")
    assert calls["title"] == "Health check failed: core.synthetic"
    assert health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=command_text,
        )
    ) in calls["body"]
    assert "token=[REDACTED]" in calls["body"]
    assert "secret-value" not in calls["body"]


def test_health_github_reporting_suppresses_duplicate_failure(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    fingerprint = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    health_reporting._touch_failure_report(fingerprint)
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: pytest.fail("cooldown should suppress issue lookup"),
    )
    monkeypatch.setattr(
        github_service,
        "create_issue",
        lambda **kwargs: pytest.fail("cooldown should suppress duplicate create"),
    )

    assert (
        health_reporting.report_health_check_failure(
            definition=definition,
            failure_message="failed",
            command_text=command_text,
        )
        is None
    )


def test_health_github_reporting_updates_existing_failure_issue(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=command_text,
        )
    )
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 23,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/23",
                "body": f"Existing issue\n\n{marker}",
            }
        ],
    )
    comments: list[dict[str, object]] = []
    monkeypatch.setattr(
        github_service,
        "create_issue",
        lambda **kwargs: pytest.fail("existing issue should receive a comment"),
    )

    def fake_comment(owner, repository, **kwargs):
        comments.append({"owner": owner, "repository": repository, **kwargs})
        return DummyResponse({"id": 1})

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)

    issue_url = health_reporting.report_health_check_failure(
        definition=definition,
        failure_message="Synthetic failed",
        command_text=command_text,
    )

    assert issue_url == "https://github.com/octo/demo/issues/23"
    assert comments[0]["issue_number"] == 23
    assert "failed again" in comments[0]["body"]


def test_health_github_reporting_finds_existing_issue_after_first_page(
    monkeypatch, settings, tmp_path
) -> None:
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=command_text,
        )
    )
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    issues = [
        {"number": number, "state": "open", "body": ""}
        for number in range(1, 102)
    ]
    issues.append(
        {
            "number": 102,
            "state": "open",
            "html_url": "https://github.com/octo/demo/issues/102",
            "body": f"Existing issue\n\n{marker}",
        }
    )
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: iter(issues),
    )
    comments: list[dict[str, object]] = []
    monkeypatch.setattr(
        github_service,
        "create_issue",
        lambda **kwargs: pytest.fail("existing issue should be found"),
    )

    def fake_comment(owner, repository, **kwargs):
        comments.append({"owner": owner, "repository": repository, **kwargs})
        return DummyResponse({"id": 1})

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)

    issue_url = health_reporting.report_health_check_failure(
        definition=definition,
        failure_message="Synthetic failed",
        command_text=command_text,
    )

    assert issue_url == "https://github.com/octo/demo/issues/102"
    assert comments[0]["issue_number"] == 102


def test_health_github_reporting_closes_recovered_issue(
    monkeypatch, settings, tmp_path
) -> None:
    settings.NODE_ROLE = "Terminal"
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._positive_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    fingerprint = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    marker = health_reporting.health_check_fingerprint_marker(fingerprint)
    health_reporting._touch_failure_report(fingerprint)
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 22,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/22",
                "body": f"Existing issue\n\n{marker}",
            }
        ],
    )
    comments: list[dict[str, object]] = []
    closed: list[int] = []

    def fake_comment(owner, repository, **kwargs):
        comments.append({"owner": owner, "repository": repository, **kwargs})
        return DummyResponse({"id": 1})

    def fake_close(**kwargs):
        closed.append(kwargs["issue_number"])
        return DummyResponse({"state": "closed"})

    monkeypatch.setattr(github_service, "create_issue_comment", fake_comment)
    monkeypatch.setattr(github_service, "close_issue", fake_close)

    issue_url = health_reporting.report_health_check_recovery(
        definition=definition,
        command_text=command_text,
    )

    assert issue_url == "https://github.com/octo/demo/issues/22"
    assert comments[0]["issue_number"] == 22
    assert "recovered" in comments[0]["body"]
    assert closed == [22]
    assert not health_reporting._lock_path(fingerprint).exists()


def test_health_github_reporting_recovery_clears_missing_issue_lock(
    monkeypatch, settings, tmp_path
) -> None:
    settings.NODE_ROLE = "Terminal"
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._positive_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    fingerprint = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    health_reporting._touch_failure_report(fingerprint)
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(github_service, "fetch_repository_issues", lambda **kwargs: [])

    issue_url = health_reporting.report_health_check_recovery(
        definition=definition,
        command_text=command_text,
    )

    assert issue_url is None
    assert not health_reporting._lock_path(fingerprint).exists()


def test_health_github_reporting_recovery_ignores_other_node_issue(
    monkeypatch, settings, tmp_path
) -> None:
    settings.NODE_ROLE = "Terminal"
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._positive_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")
    other_marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=command_text,
        )
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-b")
    local_marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=command_text,
        )
    )
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 22,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/22",
                "body": f"Existing issue\n\n{other_marker}",
            }
        ],
    )
    monkeypatch.setattr(
        github_service,
        "create_issue_comment",
        lambda *args, **kwargs: pytest.fail("other node issue must not be commented"),
    )
    monkeypatch.setattr(
        github_service,
        "close_issue",
        lambda **kwargs: pytest.fail("other node issue must not be closed"),
    )

    issue_url = health_reporting.report_health_check_recovery(
        definition=definition,
        command_text=command_text,
    )

    assert local_marker != other_marker
    assert issue_url is None


def test_health_github_reporting_recovery_ignores_other_option_issue(
    monkeypatch, settings, tmp_path
) -> None:
    settings.NODE_ROLE = "Terminal"
    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.rfid",
        group="core",
        description="Validate an RFID value",
        runner=f"{__name__}._positive_health_runner",
    )
    monkeypatch.setattr(health_reporting.socket, "gethostname", lambda: "node-a")
    failing_command = (
        "manage.py health --target core.rfid --rfid-value 123 "
        "--rfid-kind badge --report-github"
    )
    passing_command = (
        "manage.py health --target core.rfid --rfid-value 456 "
        "--rfid-kind card --report-github"
    )
    other_marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=failing_command,
        )
    )
    local_marker = health_reporting.health_check_fingerprint_marker(
        health_reporting.health_check_fingerprint(
            definition,
            command_text=passing_command,
        )
    )
    github_service = _patch_health_issue_client(monkeypatch)
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        github_service,
        "fetch_repository_issues",
        lambda **kwargs: [
            {
                "number": 22,
                "state": "open",
                "html_url": "https://github.com/octo/demo/issues/22",
                "body": f"Existing issue\n\n{other_marker}",
            }
        ],
    )
    monkeypatch.setattr(
        github_service,
        "create_issue_comment",
        lambda *args, **kwargs: pytest.fail(
            "other option issue must not be commented"
        ),
    )
    monkeypatch.setattr(
        github_service,
        "close_issue",
        lambda **kwargs: pytest.fail("other option issue must not be closed"),
    )

    issue_url = health_reporting.report_health_check_recovery(
        definition=definition,
        command_text=passing_command,
    )

    assert local_marker != other_marker
    assert issue_url is None


def test_health_github_reporting_handles_missing_token(
    monkeypatch, caplog, settings, tmp_path
) -> None:
    from apps.repos.services.github import GitHubIssue, GitHubRepositoryError

    settings.BASE_DIR = tmp_path
    definition = HealthCheckDefinition(
        target="core.synthetic",
        group="ocpp",
        description="Synthetic health diagnostics",
        runner=f"{__name__}._failing_health_runner",
    )
    command_text = "manage.py health --target core.synthetic --report-github"
    fingerprint = health_reporting.health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    monkeypatch.setattr(health_reporting, "_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        GitHubIssue,
        "from_active_repository",
        classmethod(
            lambda cls: (_ for _ in ()).throw(GitHubRepositoryError("missing token"))
        ),
    )

    with caplog.at_level("WARNING"):
        issue_url = health_reporting.report_health_check_failure(
            definition=definition,
            failure_message="failed",
            command_text=command_text,
        )

    assert issue_url is None
    assert "missing token" in caplog.text
    assert not health_reporting._lock_path(fingerprint).exists()


def test_health_skips_control_hardware_on_non_control_role(settings) -> None:
    settings.NODE_ROLE = "Watchtower"
    settings.INSTALLED_APPS = ["apps.core", "apps.cards"]
    stdout = StringIO()

    call_command(
        "health", "--target", "core.rfid", "--rfid-value", "123", stdout=stdout
    )

    output = stdout.getvalue()
    assert (
        "[skipped] core.rfid: node role Watchtower is not eligible; expected: Control"
        in output
    )
    assert "Health checks passed." in output


def test_health_resolves_string_runner_for_enabled_target(
    monkeypatch, settings
) -> None:
    settings.INSTALLED_APPS = ["apps.core"]
    monkeypatch.setitem(
        __import__(
            "apps.core.management.commands.health",
            fromlist=["HEALTH_CHECKS"],
        ).HEALTH_CHECKS,
        "core.positive_runner",
        HealthCheckDefinition(
            target="core.positive_runner",
            group="core",
            description="Exercise dotted runner import resolution",
            runner=f"{__name__}._positive_health_runner",
            app_selector="apps.core",
        ),
    )
    stdout = StringIO()

    call_command("health", "--target", "core.positive_runner", stdout=stdout)

    output = stdout.getvalue()
    assert "[skipped]" not in output
    assert "Positive runner executed." in output
    assert "Health checks passed." in output
