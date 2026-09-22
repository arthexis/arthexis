from pathlib import Path
from unittest import TestCase


class WatchtowerWorkflowTests(TestCase):
    def test_public_markdown_root_is_asserted_after_exposure(self) -> None:
        workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "grep -F '<h2 id=\"operational-capabilities\">Operational Capabilities</h2>'",
            workflow,
        )

        self.assertIn("Verify public Markdown root", workflow)
        self.assertIn(
            "curl --fail --silent --show-error --retry 5 --retry-delay 1 "
            "--retry-all-errors https://arthexis.com/",
            workflow,
        )
        self.assertIn(
            "grep -F '<h1 id=\"constellation\">Constellation</h1>'",
            workflow,
        )

    def test_public_watchtower_logs_are_minimal(self) -> None:
        workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
            encoding="utf-8"
        )
        start = workflow.index("- name: Expose Arthexis publicly through Gway recipe")
        public_workflow = workflow[start:]

        self.assertIn(
            "ARTHEXIS_CERTBOT_EMAIL: ${{ secrets.ARTHEXIS_CERTBOT_EMAIL }}",
            public_workflow,
        )
        self.assertNotIn(
            "ARTHEXIS_CERTBOT_EMAIL: ${{ vars.ARTHEXIS_CERTBOT_EMAIL }}",
            public_workflow,
        )

        for forbidden in (
            "systemctl status",
            "journalctl",
            "managed_path=",
            "data_path=",
            "python --version",
            "pip check 2>&1",
            "Capture deployment evidence",
            "Upload deployment evidence",
            "actions/upload-artifact",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, public_workflow)

        self.assertIn('echo "service=active"', workflow)

        for status in (
            'echo "public_exposure=ok"',
            'echo "public_root=ok"',
            'echo "django_check=ok"',
            'echo "migration_drift=none"',
            'echo "ocpp_matrix=ok"',
        ):
            with self.subTest(status=status):
                self.assertIn(status, public_workflow)
