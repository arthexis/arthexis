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
            "CRI apps.demo: Panic failure",
        ]
    )

    output = LocalLLMSummarizer().summarize(prompt)

    assert "LOG 1" not in output
    assert "6 ln       ERROR" in output
    assert "Panic failure" in output
    assert "HB OK" in output
    assert "OCPP FWD" in output
    assert "2x        NORMAL" in output


def test_local_lcd_summary_reports_quiet_logs() -> None:
    output = LocalLLMSummarizer().summarize("LOGS:\n[celery.log]\n")

    assert output == "No recent logs\n0 ln      NORMAL"


def test_local_lcd_summary_keeps_journal_failure_on_first_row() -> None:
    output = LocalLLMSummarizer().summarize(
        "LOGS:\nERR apps.demo: Journal failed 3\n"
    )

    assert output.split("\n---\n")[0] == "Journal failed 3\n1 ln       ERROR"
    assert "Check logs\n1x           FIX" in output
    assert output.count("Journal failed 3") == 1
