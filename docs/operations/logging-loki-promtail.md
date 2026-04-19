# Loki + Promtail integration for Arthexis

Use this guide to connect Arthexis operational logs to Grafana Loki with Promtail shipping and Grafana dashboards.

## 1) Enable JSON logs and shared log directory

Set these environment variables in your runtime units:

```bash
ARTHEXIS_LOG_FORMAT=json
ARTHEXIS_LOG_DIR=/var/log/arthexis
```

Arthexis already supports this mode and emits stable JSON keys appropriate for LogQL pipelines.

## 2) Deploy Loki and Promtail

Reference configs are provided in:

- `deploy/observability/loki/loki-config.yml`
- `deploy/observability/loki/promtail.yml`

Promtail should read from the same `ARTHEXIS_LOG_DIR` used by Arthexis processes.

## 3) Configure Arthexis integration URLs

Set these for admin hand-off links and integration status in Log Viewer:

```bash
ARTHEXIS_GRAFANA_URL=https://grafana.example.com
ARTHEXIS_LOKI_URL=https://loki.example.com
ARTHEXIS_PROMTAIL_CONFIG=/etc/promtail/promtail.yml
```

When all three are set, Log Viewer reports the external stack as connected.

## 4) Import starter dashboard

Import:

- `deploy/observability/grafana/arthexis-logs-overview.json`

This dashboard includes:

- Error/critical trend panel
- Warning trend panel
- Live logs panel with JSON parsing

## 5) Validate from Arthexis

Run:

```bash
.venv/bin/python manage.py logging_profile
```

Then open Admin → Log viewer and confirm integration status and Grafana link.
