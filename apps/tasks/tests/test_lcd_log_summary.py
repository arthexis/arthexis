from apps.tasks.tasks import LocalLLMSummarizer


def test_local_lcd_summary_uses_dense_event_labels() -> None:
    prompt = "\n".join(
        [
            "LOGS:",
            "[celery.log]",
            "INF celery.beat: Scheduler: Sending due task heartbeat "
            "(apps.core.tasks.heartbeat)",
            "INF celery.app.trace: Task apps.core.tasks.heartbeat[abc] "
            "succeeded in 0.01s: None",
            "INF celery.beat: Scheduler: Sending due task ocpp_forwarding_push "
            "(apps.ocpp.tasks.setup_forwarders)",
            "WRN apps.demo: Disk nearly full",
            "ERR apps.demo: Boom failure",
        ]
    )

    output = LocalLLMSummarizer().summarize(prompt)

    assert "LOG 1" not in output
    assert "ERR 1 WRN 1" in output
    assert "HB ok" in output
    assert "OCPP fwd" in output


def test_local_lcd_summary_reports_quiet_logs() -> None:
    output = LocalLLMSummarizer().summarize("LOGS:\n[celery.log]\n")

    assert output == "Quiet\nNo new logs\n---"
