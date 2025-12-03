import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache

from apps.rates.models import RateLimit
from apps.rates.services import RateLimiter
from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_rate_cache():
    cache.clear()


def test_rate_limiter_uses_fallback_when_no_rule():
    limiter = RateLimiter(target=None, fallback_limit=1, fallback_window=60)

    assert limiter.is_allowed("client-1") is True
    assert limiter.is_allowed("client-1") is False
    assert limiter.is_allowed("client-2") is True


def test_rate_limiter_honors_model_rule():
    ct = ContentType.objects.get_for_model(Charger)
    RateLimit.objects.create(content_type=ct, scope_key="connect", limit=1, window_seconds=120)

    limiter = RateLimiter(target=Charger, scope_key="connect", fallback_limit=5)

    assert limiter.is_allowed("127.0.0.1") is True
    assert limiter.is_allowed("127.0.0.1") is False
    assert limiter.is_allowed("192.168.0.2") is True
