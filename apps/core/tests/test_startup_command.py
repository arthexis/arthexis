import json
from io import StringIO

from django.core.management import call_command
from django.test import override_settings


def test_startup_command_reports_timing_breakdown(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    (lock_dir / "startup_duration.lck").write_text(
        json.dumps(
            {
                "started_at": "2026-04-18T17:03:53+00:00",
                "finished_at": "2026-04-18T17:04:46+00:00",
                "duration_seconds": 53,
                "status": 0,
                "port": "8888",
                "phase_timings": [
                    {
                        "name": "runtime_bootstrap",
                        "duration_ms": 950,
                        "status": "completed",
                    },
                    {
                        "name": "startup_orchestrate",
                        "duration_ms": 15123,
                        "status": "completed",
                    },
                    {
                        "name": "readiness_wait",
                        "duration_ms": 36750,
                        "status": "completed",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (lock_dir / "startup_orchestrate_status.lck").write_text(
        json.dumps(
            {
                "started_at": "2026-04-18T17:03:53+00:00",
                "finished_at": "2026-04-18T17:04:08+00:00",
                "duration_seconds": 15,
                "status": 0,
                "phase": "orchestration",
                "port": "8888",
                "phase_timings": [
                    {
                        "name": "runserver_preflight",
                        "duration_ms": 14250,
                        "status": "ok",
                        "detail": "Database matches cached migrations fingerprint; skipping migration checks.",
                    },
                    {
                        "name": "startup_maintenance",
                        "duration_ms": 110,
                        "status": "ok",
                        "detail": "ok",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    stdout = StringIO()
    with override_settings(BASE_DIR=str(tmp_path)):
        call_command("startup", "--timings", stdout=stdout)

    output = stdout.getvalue()
    assert "Startup timing summary:" in output
    assert "Measured readiness window: 53.000s" in output
    assert "Service-start phases:" in output
    assert "runtime_bootstrap: 0.950s [completed]" in output
    assert "startup_orchestrate: 15.123s [completed]" in output
    assert "Orchestration phase:" in output
    assert "runserver_preflight: 14.250s [ok]" in output
