from pathlib import Path

import pytest


pytestmark = pytest.mark.workflow

WORKFLOW = Path(".github/workflows/watchtower-deploy.yml")


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_queue_coalescing_surfaces_explicit_deferred_deployment_job():
    text = _workflow()

    assert "name: Deployment deferred — active PR queue" in text
    assert "No deployment or certification occurred" in text
    assert "reason=active-pr-queue" in text


def test_product_install_failure_uses_private_queryable_diagnostic_snapshot():
    text = _workflow()
    start = text.index("      - name: Deploy Arthexis through Gway system scope")
    end = text.index("      - name: Prepare persistent Arthexis runtime", start)
    block = text[start:end]

    assert "scripts/watchtower_diagnostics.py" in block
    assert '--step "arthexis-product-install"' in block
    assert "diagnostic_source=recipe/watchtower-deploy" in block
    assert 'cat "${install_log}"' not in block


def test_runtime_install_failure_uses_private_queryable_diagnostic_snapshot():
    text = _workflow()
    start = text.index("      - name: Prepare persistent Arthexis runtime")
    end = text.index("      - name: Install canonical Watchtower Gway admin launcher", start)
    block = text[start:end]

    assert "scripts/watchtower_diagnostics.py" in block
    assert '--step "prepare-arthexis-runtime"' in block
    assert "diagnostic_source=recipe/watchtower-deploy" in block
    assert "/opt/arthexis/install.sh >/dev/null 2>&1" not in block
