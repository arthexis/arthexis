from apps.sites.admin.reports_admin import _observability_integration_status


def test_observability_status_reports_not_configured(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_GRAFANA_URL", raising=False)
    monkeypatch.delenv("ARTHEXIS_LOKI_URL", raising=False)
    monkeypatch.delenv("ARTHEXIS_PROMTAIL_CONFIG", raising=False)

    status = _observability_integration_status()

    assert status["configured"] is False
    assert status["grafana_url"] == ""


def test_observability_status_reports_configured(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_GRAFANA_URL", "https://grafana.example.test")
    monkeypatch.setenv("ARTHEXIS_LOKI_URL", "https://loki.example.test")
    monkeypatch.setenv("ARTHEXIS_PROMTAIL_CONFIG", "/etc/promtail/promtail.yml")

    status = _observability_integration_status()

    assert status["configured"] is True
    assert status["grafana_url"] == "https://grafana.example.test"
