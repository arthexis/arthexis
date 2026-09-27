from pathlib import Path

from apps.ocpp.protocol.registry import ALL_ACTIONS


def test_readme_lists_every_retained_ocpp_action() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for action in {contract.action for contract in ALL_ACTIONS}:
        assert f"`{action}`" in readme


def test_public_readme_mentions_gway_only_for_installation() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    lower = readme.lower()

    assert "https://github.com/arthexis/gway" in readme
    assert "#### Option 2: Install with Gway" in readme
    assert "## Development" not in readme
    assert "arthexis-rebuild" not in lower


def test_readme_retains_constellation_sections() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for heading in (
        "# Constellation",
        "## Purpose",
        "## Suite Features",
        "## Role Architecture",
        "## Quick Guide",
        "## Support",
    ):
        assert heading in readme


def test_readme_documents_operational_capabilities() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for capability in (
        "## Operational Capabilities",
        "**Fleet inspection**",
        "**Charger enrollment**",
        "**Authorization policy**",
        "**Explicit charger control**",
        "**Operation correlation**",
    ):
        assert capability in readme


def test_readme_documents_typed_charger_controls() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for operation in (
        "`RemoteStartTransaction`",
        "`RequestStartTransaction`",
        "`RemoteStopTransaction`",
        "`RequestStopTransaction`",
    ):
        assert operation in readme


def test_readme_avoids_product_version_migration_framing() -> None:
    readme = Path("README.md").read_text(encoding="utf-8").lower()

    for phrase in (
        "current 2.0",
        "fresh 2.0",
        "frozen 1.x",
        "clean reimplementation",
        "legacy reconciliation",
    ):
        assert phrase not in readme


def test_event_system_is_documented_outside_base_readme() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    events = Path("docs/events.md").read_text(encoding="utf-8")

    assert "**Structured events**" not in readme
    assert "### Publish a structured event" not in readme
    for contract in (
        "# Events and Celery",
        "`ocpp.meter_values.received`",
        "`discovery.event`",
        "`events.dispatch_pending`",
        "`events.process`",
    ):
        assert contract in events
