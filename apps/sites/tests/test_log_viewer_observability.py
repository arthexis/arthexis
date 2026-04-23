from apps.sites.admin.reports_admin import _observability_integration_status


def test_observability_status_hides_unsafe_urls(settings):
    settings.ARTHEXIS_GRAFANA_URL = "javascript:alert(1)"
    settings.ARTHEXIS_LOKI_URL = "ftp://loki.example.test"
    settings.ARTHEXIS_PROMTAIL_CONFIG = "/etc/promtail/promtail.yml"

    status = _observability_integration_status()

    assert status["configured"] is False
    assert status["grafana_url"] == ""
    assert status["loki_url"] == ""
