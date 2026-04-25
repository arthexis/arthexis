from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "arthexis-cleanup-step"
    / "scripts"
    / "turn_boundary.py"
)


def load_turn_boundary():
    spec = importlib.util.spec_from_file_location("turn_boundary", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cadence_rest_deducts_elapsed_turn_time():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)
    now = started_at + dt.timedelta(seconds=215)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 600, now=now)

    assert rest == 385


def test_cadence_rest_is_zero_after_cadence_elapsed():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)
    now = started_at + dt.timedelta(seconds=601)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 600, now=now)

    assert rest == 0


def test_cadence_rest_is_zero_when_cadence_disabled():
    module = load_turn_boundary()
    started_at = dt.datetime(2026, 4, 25, 8, 0, tzinfo=dt.timezone.utc)

    rest = module.cadence_rest_seconds({"started_at": started_at.isoformat()}, 0, now=started_at)

    assert rest == 0
