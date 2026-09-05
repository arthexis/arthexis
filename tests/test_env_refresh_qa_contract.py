from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_refresh_exports_qa_requirements_flag() -> None:
    script_text = (ROOT / "env-refresh.sh").read_text(encoding="utf-8")

    assert (
        'export ARTHEXIS_INCLUDE_QA_REQUIREMENTS="$INCLUDE_QA_REQUIREMENTS"'
        in script_text
    )
