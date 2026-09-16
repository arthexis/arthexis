from pathlib import Path

import toml


def test_arthexis_has_one_role_independent_service_topology() -> None:
    manifest = toml.load(Path(__file__).resolve().parents[3] / "gway.toml")
    services = manifest["services"]

    assert set(services) == {"web-local", "worker", "beat"}
    assert all("profiles" not in config for config in services.values())
    assert services["web-local"]["command"][-2:] == [
        "127.0.0.1:8888",
        "--noreload",
    ]
