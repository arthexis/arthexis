import pytest
from django.test import RequestFactory

from apps.docs import views


@pytest.mark.parametrize("query", ["controller=1", "tv=true", "ps4", "ps4=on"])
def test_controller_query_flags_force_full_document(query):
    request = RequestFactory().get(f"/docs/?{query}")

    assert views._should_force_controller_full_document(request) is True


@pytest.mark.parametrize("query", ["controller=0", "tv=false", "ps4=off", "ps4=no"])
def test_controller_query_flags_allow_opt_out(query):
    request = RequestFactory().get(f"/docs/?{query}")

    assert views._should_force_controller_full_document(request) is False
