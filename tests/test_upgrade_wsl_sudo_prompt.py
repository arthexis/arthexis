"""Regression coverage for non-interactive upgrade safety checks.

These tests intentionally validate script text rather than executing ``upgrade.sh``.
The goal is to lock in two historical fixes that protect unattended workflows:

* ``upgrade.sh --check`` must not attempt to prime sudo credentials.
* generated merge migrations are cleaned before dirty-tree checks.
"""

from pathlib import Path


def test_upgrade_script_skips_sudo_priming_for_check_mode() -> None:
    """Keep ``--check`` read-only so CI/automation does not block on sudo prompts."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert '. "$BASE_DIR/scripts/helpers/common.sh"' in script_text

    parse_start = script_text.index("while [[ $# -gt 0 ]]; do\n")
    parse_done = script_text.index("done\n", parse_start)
    gate = (
        "if [[ $CHECK_ONLY -ne 1 ]]; then\n"
        "  arthexis_prime_sudo_credentials >/dev/null 2>&1 || true\n"
        "fi"
    )

    assert gate in script_text
    assert script_text.index(gate) > parse_done


def test_upgrade_script_cleans_generated_merge_migrations() -> None:
    """Regression: remove generated merge migrations before dirty-tree checks."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert "Removing generated migration" in script_text
    assert "^apps/[^/]+/migrations/[0-9]+_merge_[0-9]{8}_[0-9]{4}\\.py$" in script_text


def test_upgrade_script_supports_short_flag_aliases() -> None:
    """Regression: ``-f`` and ``-t`` should map to force/latest upgrade flows."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert "--latest|--unstable|-l|-t" in script_text
    assert "--force|-f" in script_text


def test_upgrade_script_auto_reruns_after_self_update() -> None:
    """Regression: upgraded script should re-exec once instead of requiring manual rerun."""

    script_text = Path("upgrade.sh").read_text(encoding="utf-8")

    assert "rerun_with_updated_script()" in script_text
    assert "ARTHEXIS_UPGRADE_SELF_UPDATE_DEPTH" in script_text
    assert "restarting upgrade automatically with the new script" in script_text
    assert "if rerun_with_updated_script; then" in script_text
    assert "please run the upgrade again to use the new script" in script_text
