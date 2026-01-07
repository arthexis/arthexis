# LLM LCD log summary

The `summary` domain ships a local LLM-backed workflow that produces extremely sparse LCD summaries for recent system logs. The feature is gated behind the **LLM Summary** node feature (`llm-summary`) and is automatically enabled only when LCD and Celery locks are present.

## How it works

- A periodic Celery task (`summary.tasks.generate_lcd_log_summary`) runs every five minutes when the node feature is enabled.
- The task reads log files under the configured log directory and only ingests content that has changed since the prior run.
- Logs are compacted deterministically (timestamps, UUIDs, IPs, and long hex sequences are normalized) before being passed to the LLM.
- The prompt directs the model to emit 8–10 short subject/body pairs sized for a 16x2 LCD, favoring shorthand and symbols.
- Each subject/body pair is written to the low LCD lock file every 30 seconds.

## Configuration

The `LLM Summary Config` admin record stores the cursor state and local model configuration:

- **Model path** – directory containing the local LLM model. Defaults to `work/llm/lcd-summary`.
- **Model command** – optional shell command used to run the model. If omitted, a deterministic fallback summarizer is used.
- **Last prompt/output** – captured to aid debugging when output needs auditing.

Environment overrides:

- `ARTHEXIS_LLM_SUMMARY_MODEL` – alternate model directory.
- `LLM_SUMMARY_COMMAND` – command to invoke the local model.
- `LLM_SUMMARY_TIMEOUT` – prompt timeout in seconds (defaults to 240).

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
