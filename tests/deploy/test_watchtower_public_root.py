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
            "ARTHEXIS_CERTBOT_EMAIL: ${{ vars.ARTHEXIS_CERTBOT_EMAIL }}",
            public_workflow,
        )
        self.assertNotIn(
            "ARTHEXIS_CERTBOT_EMAIL: ${{ secrets.ARTHEXIS_CERTBOT_EMAIL }}",
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



def test_watchtower_public_exposure_reuses_primary_edge_ipv4_for_remote_dns() -> None:
    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "getent ahostsv4 arthexis.com" in workflow
    assert "getent ahostsv4 remote.arthexis.com" in workflow
    assert "./deploy/remote-dns.rx" in workflow
    assert "--public-ipv4 " in workflow



def test_watchtower_remote_provision_failure_keeps_safe_diagnostics() -> None:
    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert 'echo "remote_provision=failed"' in workflow
    assert "systemctl status gway-mcp-server.service --no-pager" in workflow
    assert "systemctl status gway-remote-auth.service --no-pager" in workflow
    assert "journalctl -u gway-mcp-server.service -n 50 --no-pager" in workflow
    assert "journalctl -u gway-remote-auth.service -n 50 --no-pager" in workflow



def test_watchtower_dns_bootstrap_preserves_legacy_godaddy_credentials() -> None:
    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "ACTIONS_GODADDY_PAT: ${{ secrets.GODADDY_PAT }}" in workflow
    assert (
        "ACTIONS_GODADDY_API_KEY: "
        "${{ secrets.GODADDY_API_KEY || vars.GODADDY_API_KEY }}"
    ) in workflow
    assert "ACTIONS_GODADDY_API_SECRET: ${{ secrets.GODADDY_API_SECRET }}" in workflow
    assert 'godaddy_pat="${ACTIONS_GODADDY_PAT:-${GODADDY_PAT:-}}"' in workflow
    assert 'godaddy_key="${ACTIONS_GODADDY_API_KEY:-${GODADDY_API_KEY:-}}"' in workflow
    assert (
        'godaddy_secret="${ACTIONS_GODADDY_API_SECRET:-${GODADDY_API_SECRET:-}}"'
        in workflow
    )
    assert 'GODADDY_PAT="${godaddy_pat}"' in workflow
    assert 'GODADDY_API_KEY="${godaddy_key}"' in workflow
    assert 'GODADDY_API_SECRET="${godaddy_secret}"' in workflow


def test_watchtower_dns_and_exposure_failures_keep_bounded_diagnostics() -> None:
    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert 'echo "dns_bootstrap=failed"' in workflow
    assert 'tail -n 20 "${dns_errors}"' in workflow
    assert 'echo "public_exposure=failed"' in workflow
    assert 'tail -n 30 "${exposure_errors}"' in workflow
    assert 'echo "dns_credentials=missing"' in workflow
    assert "set -x" not in workflow
