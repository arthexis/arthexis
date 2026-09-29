from pathlib import Path

import pytest

from tests.deploy.workflow_contracts import WorkflowText


@pytest.fixture(scope="session")
def watchtower_deploy_workflow() -> str:
    return Path(".github/workflows/watchtower-deploy.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def watchtower_recovery_workflow() -> str:
    return Path(".github/workflows/watchtower-recovery.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def watchtower_workflow() -> WorkflowText:
    return WorkflowText.load(".github/workflows/watchtower-deploy.yml")
