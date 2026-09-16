from pathlib import Path

import toml


def test_arthexis_role_selects_internal_service_profile() -> None:
    manifest = toml.load(Path(__file__).resolve().parents[3] / "gway.toml")
    project = manifest["project"]
    services = manifest["services"]

    assert project["service_profile_file"] == ".locks/role.lck"
    assert set(services) == {"web-local", "web-edge", "worker", "beat"}

    assert services["web-local"]["profiles"] == ["Terminal", "Watchtower"]
    assert services["web-local"]["command"][-2:] == [
        "127.0.0.1:8888",
        "--noreload",
    ]

    assert services["web-edge"]["profiles"] == ["Control", "Satellite"]
    assert services["web-edge"]["command"][-2:] == [
        "0.0.0.0:8888",
        "--noreload",
    ]

    assert services["worker"]["profiles"] == [
        "Control",
        "Satellite",
        "Watchtower",
    ]
    assert services["beat"]["profiles"] == [
        "Control",
        "Satellite",
        "Watchtower",
    ]
