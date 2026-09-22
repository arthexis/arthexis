from pathlib import Path

from django.test import SimpleTestCase

from apps.ocpp.protocol.registry import ALL_ACTIONS


class ReadmeContractTests(SimpleTestCase):
    def test_readme_lists_every_retained_ocpp_action(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for action in {contract.action for contract in ALL_ACTIONS}:
            with self.subTest(action=action):
                self.assertIn(f"`{action}`", readme)

    def test_public_readme_mentions_gway_only_for_installation(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        lower = readme.lower()

        self.assertIn("https://github.com/arthexis/gway", readme)
        self.assertIn("#### Option 2: Install with Gway", readme)
        self.assertNotIn("## Development", readme)
        self.assertNotIn("arthexis-rebuild", lower)

    def test_readme_retains_constellation_sections(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for heading in (
            "# Constellation",
            "## Purpose",
            "## Suite Features",
            "## Role Architecture",
            "## Quick Guide",
            "## Support",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, readme)

    def test_readme_documents_operational_capabilities(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for capability in (
            "## Operational Capabilities",
            "**Fleet inspection**",
            "**Charger enrollment**",
            "**Authorization policy**",
            "**Explicit charger control**",
            "**Operation correlation**",
            "**Structured events**",
        ):
            with self.subTest(capability=capability):
                self.assertIn(capability, readme)

    def test_readme_documents_typed_charger_controls(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for operation in (
            "`RemoteStartTransaction`",
            "`RequestStartTransaction`",
            "`RemoteStopTransaction`",
            "`RequestStopTransaction`",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, readme)


    def test_readme_avoids_product_version_migration_framing(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8").lower()

        for phrase in (
            "current 2.0",
            "fresh 2.0",
            "frozen 1.x",
            "clean reimplementation",
            "legacy reconciliation",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, readme)
