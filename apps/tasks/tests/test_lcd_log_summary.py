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
    assert "ERR 2 WRN 1" in output
    assert "HB ok" in output
    assert "OCPP fwd" in output


def test_local_lcd_summary_reports_quiet_logs() -> None:
    output = LocalLLMSummarizer().summarize("LOGS:\n[celery.log]\n")

    assert output == "Quiet\nNo new logs\n---"


def test_local_lcd_summary_renders_status_lines_directly() -> None:
    prompt = "\n".join(
        [
            "LOGS:",
            "[status]",
            "ERR journal: USB FAT sda1 x18 last 08:32",
            "OK usb: sda1 ro bastion",
            "OK host: t62C d54% m46%",
        ]
    )

    output = LocalLLMSummarizer().summarize(prompt)

    assert "ERR Journal\nUSB FAT sda1 x18" in output
    assert "USB key\nsda1 ro bastion" in output
    assert "Host\nt62C d54% m46%" in output
