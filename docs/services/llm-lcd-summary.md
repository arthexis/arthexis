# LLM LCD Summary

The LLM LCD summary service writes dense log-summary frames for the low-priority
LCD channel on Control nodes. It builds on the suite summary pipeline and keeps
the generated frames in `.locks/lcd-low`, `.locks/lcd-low-1`, and later rotation
files.

## Sources

The summarizer uses a small source registry rather than reading arbitrary files.
The default source groups are controlled by the `llm-summary-suite` feature
parameter `enabled_sources`, with `logs,state,journal` enabled by default. The
`max_source_bytes` parameter caps how much text any one source can contribute
before log compaction.

Current source groups:

* `logs`: recent `*.log` files under `settings.LOG_DIR`, using stored offsets so
  repeated runs only process newly appended text.
* `state`: bounded current-state files that help explain LCD/operator context:
  `.locks/lcd-summary*`, `.locks/lcd-channels.lck`, recent
  `logs/lcd-history-*.txt`, `.locks/rfid-scan.json`,
  `/run/arthexis-usb/devices.json`, `/etc/arthexis-usb/claims.json`,
  `.locks/startup_duration.lck`, `.locks/upgrade_duration.lck`, and
  `.locks/upgrade_in_progress.lck`.
* `journal`: warning-or-higher `journalctl` snippets for suite-owned systemd
  units plus `systemctl --failed` output when there are failed units.

The registry intentionally excludes `.env`, credentials, private keys,
databases, arbitrary `/etc` files, media uploads, and broad journal reads. Source
failures are nonfatal so a missing optional file or unavailable host command does
not block summary generation.

## Control-node boundary

The `llm-summary` node feature is assigned to the `Control` role fixture and
runtime auto-detection now checks the local node role before enabling it.
Summary generation and dense LCD frame generation both return
`skipped:non-control-node` on other node roles.

## Command

Generate dense frames immediately:

```bash
python manage.py summary --dense-lcd
```

Use `--allow-disabled-feature` only for an explicit one-off operator run when
the `llm-summary-suite` feature gate is disabled. The command still requires a
local Control node with the `llm-summary` feature.

## Recommendations

Keep the registry allowlisted and add new sources by source group, not by making
the summarizer crawl wider paths. The next useful sources are small command
snapshots for network/DNS state, Celery queue depth, release status, and
temperature/throttling state. The adaptive time window should decide how much
recent history to request, while `max_source_bytes` should remain a hard
per-source processing guardrail for overheated or overloaded systems.
