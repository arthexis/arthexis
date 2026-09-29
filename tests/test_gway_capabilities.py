from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_arthexis_publishes_gway_security_scopes_and_watch():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        document = tomllib.load(stream)

    gway = document["tool"]["gway"]
    scopes = gway["scopes"]

    assert set(scopes) == {"arthexis-read", "arthexis-write"}
    assert "watch" in scopes["arthexis-read"]["operations"]
    assert "arthexis.ocpp_status" in scopes["arthexis-read"]["operations"]
    assert "ocpp.charger.start" in scopes["arthexis-write"]["operations"]
    assert scopes["arthexis-read"]["environment"] == []
    assert scopes["arthexis-write"]["environment"] == []

    assert gway["watch"] == [
        {
            "section": "arthexis",
            "command": ["arthexis", "ocpp", "status"],
        }
    ]
