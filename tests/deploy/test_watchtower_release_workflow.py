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
