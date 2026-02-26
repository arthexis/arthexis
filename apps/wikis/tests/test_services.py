import pytest
from django.core.cache import cache

from apps.wikis import services
from apps.wikis.models import WikimediaBridge

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()

def _mock_response(payload, status_code=200):
    class DummyResponse:
        def __init__(self, data, code):
            self._data = data
            self.status_code = code

        def json(self):
            return self._data

        def close(self):
            return None

    return DummyResponse(payload, status_code)

