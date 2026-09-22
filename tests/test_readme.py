from pathlib import Path

from django.test import SimpleTestCase

from apps.ocpp.protocol.registry import ALL_ACTIONS


class ReadmeContractTests(SimpleTestCase):
    def test_readme_lists_every_retained_ocpp_action(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for action in {contract.action for contract in ALL_ACTIONS}:
            with self.subTest(action=action):
                self.assertIn(f"`{action}`", readme)

    def test_public_readme_does_not_describe_gway(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8").lower()

        self.assertNotIn("gway", readme)

    def test_readme_retains_constellation_sections(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        for heading in (
            "# Constellation",
            "## Purpose",
            "## Suite Features",
            "## Role Architecture",
            "## Quick Guide",
            "## Development",
            "## Support",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, readme)
