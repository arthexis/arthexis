import pytest

from apps.tests.models import SuiteTest


@pytest.mark.django_db
def test_suite_test_plural_name_uses_title_case():
    assert SuiteTest._meta.verbose_name_plural == "Suite Tests"
