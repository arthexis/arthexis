# LLM LCD Summary

The LLM LCD summary service writes dense log-summary frames for the low-priority
LCD channel on Control nodes. It builds on the suite summary pipeline and keeps
the generated frames in `.locks/lcd-low`, `.locks/lcd-low-1`, and later rotation
files.

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
