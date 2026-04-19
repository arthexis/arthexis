from io import StringIO

from django.core.management import call_command


def test_logging_profile_command_prints_runtime_settings(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_GRAFANA_URL", "https://grafana.example.test")
    monkeypatch.setenv("ARTHEXIS_LOKI_URL", "https://loki.example.test")
    monkeypatch.setenv("ARTHEXIS_PROMTAIL_CONFIG", "/etc/promtail/promtail.yml")

    stdout = StringIO()
    call_command("logging_profile", stdout=stdout)
    output = stdout.getvalue()

    assert "Logging profile" in output
    assert "formatter:" in output
    assert "ARTHEXIS_GRAFANA_URL: https://grafana.example.test" in output
