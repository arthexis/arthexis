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


def test_fetch_wiki_summary_uses_cache(monkeypatch):
    bridge = WikimediaBridge.objects.create(
        slug="wikipedia",
        name="Wikipedia",
        api_endpoint="https://example.com/w/api.php",
        user_agent="pytest-agent",
        timeout=7,
    )

    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        payload = {
            "query": {
                "pages": [
                    {
                        "title": "Django",
                        "extract": "Django is a Python-based web framework.",
                        "fullurl": "https://example.com/wiki/Django",
                    }
                ]
            }
        }
        return _mock_response(payload)

    monkeypatch.setattr(services.requests, "get", fake_get)

    first = services.fetch_wiki_summary("Django")
    second = services.fetch_wiki_summary("Django")

    assert first == second
    assert first and first.extract.startswith("Django is a Python-based")
    assert len(calls) == 1
    assert calls[0]["params"]["titles"] == "Django"
    assert calls[0]["headers"]["User-Agent"] == bridge.user_agent
    assert calls[0]["timeout"] == bridge.timeout


def test_fetch_wiki_summary_handles_error(monkeypatch):
    WikimediaBridge.objects.create(
        slug="wikipedia",
        name="Wikipedia",
        api_endpoint="https://example.com/w/api.php",
    )

    def fake_get(url, params=None, headers=None, timeout=None):
        return _mock_response({}, status_code=500)

    monkeypatch.setattr(services.requests, "get", fake_get)

    assert services.fetch_wiki_summary("Unknown") is None
