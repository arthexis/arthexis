from apps.tasks.tasks import LocalLLMSummarizer, _summary_status_line


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
    assert "6 ln/60m   ERROR" in output
    assert "Panic failure" in output
    assert "HB OK" in output
    assert "OCPP FWD" in output
    assert "2x/60m    NORMAL" in output


def test_local_lcd_summary_reports_quiet_logs() -> None:
    output = LocalLLMSummarizer().summarize("LOGS:\n[celery.log]\n")

    assert output == "No recent logs\n0 ln/60m  NORMAL"


def test_local_lcd_summary_uses_prompt_window_label() -> None:
    output = LocalLLMSummarizer().summarize(
        "\n".join(
            [
                "LCD_CONTEXT_WINDOW_LABEL: 5m",
                "LOGS:",
                "ERR apps.demo: Boom failure",
            ]
        )
    )

    assert output.split("\n---\n")[0] == "Boom failure\n1 ln/5m    ERROR"


def test_local_lcd_summary_keeps_journal_failure_on_first_row() -> None:
    output = LocalLLMSummarizer().summarize("LOGS:\nERR apps.demo: Journal failed 3\n")

    assert output.split("\n---\n")[0] == "Journal failed 3\n1 ln/60m   ERROR"
    assert "Check logs\n1x/60m       FIX" in output
    assert output.count("Journal failed 3") == 1


def test_local_lcd_summary_keeps_latest_warning_detail_with_errors() -> None:
    output = LocalLLMSummarizer().summarize(
        "\n".join(
            [
                "LOGS:",
                "ERR apps.demo: Boom failure",
                "WRN apps.demo: Disk warning 1",
                "WRN apps.demo: Disk warning 2",
                "WRN apps.demo: Disk warning 3",
            ]
        )
    )

    assert "Boom failure\n4 ln/60m   ERROR" in output
    assert "Disk warning 3\n1 ln/60m WARNING" in output
    assert output.count("Boom failure") == 1


def test_summary_status_line_preserves_exact_fit_status() -> None:
    assert _summary_status_line("123456789", "WARNING") == "123456789WARNING"


def test_summary_status_line_normalizes_line_words() -> None:
    assert _summary_status_line("12 lines", "normal") == "12 ln/60m NORMAL"


def test_summary_status_line_preserves_existing_window_label() -> None:
    assert _summary_status_line("12 ln/5m", "normal") == "12 ln/5m  NORMAL"
