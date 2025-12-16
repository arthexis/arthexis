import time

import pytest

from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
@pytest.mark.parametrize("iterations, max_seconds", [(500, 0.75), (1000, 1.5)])
def test_resolve_sigils_many_env_tokens_scales_linearly(monkeypatch, iterations, max_seconds):
    SigilRoot.objects.update_or_create(
        prefix="ENV", defaults={"context_type": SigilRoot.Context.CONFIG}
    )
    monkeypatch.setenv("VALUE", "x")

    text = " ".join(["[ENV.VALUE]" for _ in range(iterations)])

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

    text = "[ENV.VALUE" * 1500

    start = time.perf_counter()
    resolved = sigil_resolver.resolve_sigils(text)
    elapsed = time.perf_counter() - start

    assert resolved.startswith("[ENV.VALUE")
    assert resolved.endswith("[ENV.VALUE")
    assert elapsed < 4
