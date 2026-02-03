"""Tests for log filename normalization."""

import pytest

from apps.loggers.filenames import normalize_log_filename

pytestmark = pytest.mark.critical

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("host name", "host_name"),
        ("host!!name", "host_name"),
        ("!hostname!", "hostname"),
        ("!@#", "arthexis"),
        (".", "arthexis"),
        ("..", "arthexis"),
        ("_._", "arthexis"),
        (".my-app", "my-app"),
    ],

)
def test_normalize_log_filename(value: str, expected: str) -> None:
    """Ensure log filenames normalize unsafe characters consistently."""

    assert normalize_log_filename(value) == expected
