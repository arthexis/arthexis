# LLM LCD log summary

Scope: this document describes an optional feature pair (`llm-summary-suite` + `llm-summary` node assignment), not suite-wide architecture.

The `summary` domain ships a local in-process workflow that produces extremely sparse LCD summaries for recent system logs. Runtime execution is gated by the **LLM Summary Suite** feature (`llm-summary-suite`) and connected to the **LLM Summary** node feature (`llm-summary`), which is only assigned by default to Control nodes.

## How it works

- A periodic Celery task (`summary.tasks.generate_lcd_log_summary`) runs every five minutes when the node feature is enabled.
- The task reads log files under the configured log directory and only ingests content that has changed since the prior run.
- Logs are compacted deterministically (timestamps, UUIDs, IPs, and long hex sequences are normalized) before being passed to the summarizer.
- The built-in deterministic backend emits 8–10 short subject/body pairs sized for a 16x2 LCD, favoring shorthand and symbols.
- Each subject/body pair is written to the low LCD lock file every 30 seconds.

## Configuration

The `LLM Summary Config` admin record stores the cursor state and safe local summary settings:

- **Summary backend** – fixed in-process backend selection. Current supported value: `deterministic`.
- **Model path** – directory reserved for local summary artifacts. Defaults to `work/llm/lcd-summary`.
- **Model command audit** – archived legacy command text retained only for review during upgrades. It is never executed.
- **Last prompt/output** – captured to aid debugging when output needs auditing.

Environment overrides:

- `ARTHEXIS_LLM_SUMMARY_MODEL` – alternate model directory.

## LCD preview

Example screen sequence (16x2 each):

```
ALERT: OCPP   
Chk EVCS 2   
---
DB WARN       
Disk 90%     
---
NET ERR       
LTE drop x3  
```

## Notes

- The LCD completes a full message cycle every 30 seconds.
- The task caps LCD output to 10 screens to keep the display cycle under five minutes.
- The feature depends on both LCD and Celery lock files, so the LCD service and Celery must be enabled.
- Summary generation no longer executes operator-provided commands from database records, suite metadata, or Django settings.

## Suite feature

- The suite feature stores centralized runtime parameters in `metadata.parameters` (`backend`, `model_path`).
- The Configure action in **Admin → Summary → LLM Summary Configs** opens a wizard with an environment checklist (suite gate, node assignment, locks, active config, backend, model directory).
- The periodic summary task is synced only when both the suite feature and node feature are enabled for the local node.
