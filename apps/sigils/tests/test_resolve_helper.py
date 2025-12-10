from __future__ import annotations

import types

import pytest

import apps.sigils


@pytest.mark.parametrize(
    "input_value, resolved, default, expected",
    [
        (None, "", "fallback", "fallback"),
        ("[ENV.MISSING]", "[ENV.MISSING]", "fallback", "fallback"),
        ("[ENV.VALUE]", "resolved", "fallback", "resolved"),
    ],
)
def test_resolve_handles_defaults(monkeypatch, input_value, resolved, default, expected):
    fake_module = types.SimpleNamespace(resolve_sigils=lambda value: resolved)
    monkeypatch.setattr(apps.sigils, "_get_resolver_module", lambda: fake_module)

    assert apps.sigils.resolve(input_value, default=default) == expected
