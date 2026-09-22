from pathlib import Path
from unittest import TestCase


class WatchtowerWorkflowTests(TestCase):
    def test_public_markdown_root_is_asserted_after_exposure(self) -> None:
        workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("Verify public Markdown root", workflow)
        self.assertIn(
            "curl --fail --silent --show-error --retry 5 --retry-delay 1 "
            "--retry-all-errors https://arthexis.com/",
            workflow,
        )
        self.assertIn(
            "grep -F '<h1 id=\"arthexis\">Arthexis</h1>'",
            workflow,
        )
