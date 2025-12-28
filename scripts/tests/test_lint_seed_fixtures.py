from pathlib import Path

import pytest
from django.conf import settings

from scripts.lint_seed_fixtures import find_missing_seed_flags


@pytest.mark.django_db
def test_seed_fixtures_include_seed_flag() -> None:
    fixtures_root = Path(settings.BASE_DIR) / "apps"

    assert find_missing_seed_flags(fixtures_root) == []
