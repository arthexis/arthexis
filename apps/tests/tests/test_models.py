import pytest

from apps.tests.models import SuiteTest


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("attribute", "expected"),
    [("verbose_name", "Suite Test"), ("verbose_name_plural", "Suite Tests")],
)
def test_suite_test_names_use_title_case(attribute, expected):
    assert getattr(SuiteTest._meta, attribute) == expected
