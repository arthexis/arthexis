from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


def _project():
    with Path("pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_arthexis_read_scope_grants_survey():
    operations = set(
        _project()["tool"]["gway"]["scopes"]["arthexis-read"]["operations"]
    )

    assert "survey" in operations
    assert "watch" not in operations


def test_arthexis_publishes_survey_contributor():
    gway = _project()["tool"]["gway"]

    assert gway["survey"] == [
        {
            "section": "arthexis",
            "command": ["arthexis", "ocpp", "status"],
        }
    ]
    assert "watch" not in gway
