from __future__ import annotations

LLM_SUMMARY_NODE_FEATURE_SLUG = "llm-summary"
LLM_SUMMARY_SUITE_FEATURE_SLUG = "llm-summary-suite"
LLM_SUMMARY_CELERY_TASK_NAME = "apps.summary.tasks.generate_lcd_log_summary"
LCD_SUMMARY_WINDOW_MINUTES = 5
LCD_SUMMARY_WINDOW_LABEL = f"{LCD_SUMMARY_WINDOW_MINUTES}m"
