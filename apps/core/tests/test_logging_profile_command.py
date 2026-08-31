from io import StringIO

from django.core.management import call_command


def test_logging_profile_command_prints_runtime_settings(settings):
    """The command should display resolved formatter and log directory."""

    stdout = StringIO()
    call_command("logging_profile", stdout=stdout)
    output = stdout.getvalue()

    assert "Logging profile" in output
    assert "formatter:" in output
    assert "log_dir:" in output
    assert "Observability wiring" not in output
