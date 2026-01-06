import pytest

from apps.summary.models import SummaryState
from apps.summary.tasks import LocalSummaryModel, _compact_lines, _log_state


@pytest.mark.django_db
def test_log_state_tracks_new_lines(tmp_path, settings):
    log_dir = tmp_path
    settings.LOG_DIR = log_dir
    settings.LOG_FILE_NAME = "test.log"

    log_file = log_dir / "test.log"
    log_file.write_text("first\nsecond\n", encoding="utf-8")

    state = SummaryState.get_default()

    lines, offsets = _log_state(state, log_dir)
    assert lines == ["[test] first", "[test] second"]
    assert offsets["test.log"] == log_file.stat().st_size

    state.log_offsets = offsets
    state.save(update_fields=["log_offsets"])

    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    lines, offsets = _log_state(state, log_dir)

    assert lines == ["[test] third"]
    assert offsets["test.log"] == log_file.stat().st_size


@pytest.mark.django_db
def test_compact_lines_reduce_noise():
    raw_lines = [
        "2024-01-01 00:00:00,000 ERROR Something bad happened",
        "2024-01-01 00:00:01,000 ERROR Something bad happened",
        "2024-01-01 00:00:02,000 INFO heartbeat ok",
    ]

    compacted = _compact_lines(raw_lines)
    assert compacted[0].startswith("ERR")
    assert compacted[0].endswith("x2")

    model = LocalSummaryModel()
    segments = model.summarize(prompt="demo", compacted_logs=compacted)

    assert 8 <= len(segments) <= 10
    for subject, body in segments:
        assert len(subject) <= 16
        assert len(body) <= 16
