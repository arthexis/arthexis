"""Domain helpers for persisting automated test results."""

from .test_results import RecordedTestResult, persist_results

__all__ = ["RecordedTestResult", "persist_results"]
