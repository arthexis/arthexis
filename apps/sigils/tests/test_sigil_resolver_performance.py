import signal
import time
from contextlib import contextmanager

import pytest

from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model


@contextmanager
def sigil_resolution_deadline(seconds: float):
    """Fail fast if sigil resolution exceeds the given deadline."""

    if not hasattr(signal, "SIGALRM"):
        # Windows lacks SIGALRM; simply run without a timer.
        yield
        return

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Sigil resolution exceeded {seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@pytest.mark.django_db
@pytest.mark.parametrize("iterations, max_seconds", [(500, 0.75), (1000, 1.5)])
def test_resolve_sigils_many_env_tokens_scales_linearly(monkeypatch, iterations, max_seconds):
    SigilRoot.objects.update_or_create(
        prefix="ENV", defaults={"context_type": SigilRoot.Context.CONFIG}
    )
    monkeypatch.setenv("VALUE", "x")

    text = " ".join(["[ENV.VALUE]" for _ in range(iterations)])

    with sigil_resolution_deadline(2):
        start = time.perf_counter()
        resolved = sigil_resolver.resolve_sigils(text)
        elapsed = time.perf_counter() - start

    assert resolved == " ".join(["x" for _ in range(iterations)])
    assert elapsed < max_seconds


@pytest.mark.django_db
def test_resolve_sigils_handles_unclosed_brackets_without_hanging():
    SigilRoot.objects.update_or_create(
        prefix="ENV", defaults={"context_type": SigilRoot.Context.CONFIG}
    )

    text = "[ENV.VALUE" * 800

    with sigil_resolution_deadline(2):
        start = time.perf_counter()
        resolved = sigil_resolver.resolve_sigils(text)
        elapsed = time.perf_counter() - start

    assert resolved.startswith("[ENV.VALUE")
    assert resolved.endswith("[ENV.VALUE")
    assert elapsed < 2


@pytest.mark.django_db
def test_resolve_sigils_raises_timeout_on_slow_entity_attribute(monkeypatch):
    User = get_user_model()
    user = User.objects.create(username="slowpoke")

    def very_slow_method(*args, **kwargs):
        time.sleep(5)
        return "done"

    monkeypatch.setattr(User, "very_slow_method", very_slow_method, raising=False)

    content_type = ContentType.objects.get_for_model(User)
    SigilRoot.objects.update_or_create(
        prefix="USR",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": content_type,
        },
    )

    with sigil_resolution_deadline(2):
        start = time.perf_counter()
        resolved = sigil_resolver.resolve_sigils("[USR.very-slow-method]")
        elapsed = time.perf_counter() - start

    assert resolved == "[USR.very-slow-method]"
    assert elapsed < 2.5
