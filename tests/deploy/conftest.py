from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def watchtower_deploy_workflow() -> str:
    return Path(".github/workflows/watchtower-deploy.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def watchtower_recovery_workflow() -> str:
    return Path(".github/workflows/watchtower-recovery.yml").read_text(encoding="utf-8")
