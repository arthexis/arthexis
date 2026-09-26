import subprocess


def test_watchtower_gway_version_awk_extracts_project_version():
    pyproject = """[build-system]
requires = ["setuptools"]

[project]
name = "gway"
version = "0.4.59"
requires-python = ">=3.10"

[project.optional-dependencies]
toml = ["tomli>=2; python_version < '3.11'"]
"""
    program = r"""
/^\[project\][[:space:]]*$/ { in_project=1; next }
/^\[/ && in_project { exit }
in_project && /^[[:space:]]*version[[:space:]]*=/ {
  value=$0
  sub(/^[^=]*=[[:space:]]*/, "", value)
  sub(/[[:space:]]*#.*/, "", value)
  gsub(/^[[:space:]]*["']|["'][[:space:]]*$/, "", value)
  print value
  exit
}
"""
    result = subprocess.run(
        ["awk", program],
        input=pyproject,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "0.4.59"


def test_watchtower_release_handoff_is_recoverable_without_gh_cli():
    from pathlib import Path

    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(encoding="utf-8")

    assert "gh workflow run" not in workflow
    assert ".watchtower/releases/{package}/{version}.json" in workflow
    assert "/actions/workflows/" in workflow
    assert "/dispatches" in workflow
    assert '_publish=already_recorded' in workflow
    assert '_publish=dispatched' in workflow


def test_watchtower_requires_release_label_before_dispatch():
    from pathlib import Path

    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(encoding="utf-8")

    assert "_publish=not_requested" in workflow
    assert "/commits/{release['sha']}/pulls" in workflow
    assert 'label.get("name") == "release"' in workflow
