from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.contrib import messages

from apps.clocks.utils import has_clock_device

if TYPE_CHECKING:  # pragma: no cover - typing imports only
    from .models import Node, NodeFeature


@dataclass(frozen=True)
class FeatureCheckResult:
    """Outcome of a feature validation."""

    success: bool
    message: str
    level: int = messages.INFO


FeatureCheck = Callable[[Any, Any], Any]


class FeatureCheckRegistry:
    """Registry for feature validation callbacks."""

    def __init__(self) -> None:
        self._checks: dict[str, FeatureCheck] = {}
        self._default_check: FeatureCheck | None = None

    def register(self, slug: str) -> Callable[[FeatureCheck], FeatureCheck]:
        """Register ``func`` as the validator for ``slug``."""

        def decorator(func: FeatureCheck) -> FeatureCheck:
            self._checks[slug] = func
            return func

        return decorator

    def register_default(self, func: FeatureCheck) -> FeatureCheck:
        """Register ``func`` as the fallback validator."""

        self._default_check = func
        return func

    def get(self, slug: str) -> FeatureCheck | None:
        return self._checks.get(slug)

    def items(self) -> Iterable[tuple[str, FeatureCheck]]:
        return self._checks.items()

    def run(
        self, feature: NodeFeature, *, node: Node | None = None
    ) -> FeatureCheckResult | None:
        check = self._checks.get(feature.slug)
        if check is None:
            check = self._default_check
            if check is None:
                return None
        result = check(feature, node)
        return self._normalize_result(feature, result)

    def _normalize_result(
        self, feature: NodeFeature, result: Any
    ) -> FeatureCheckResult:
        if isinstance(result, FeatureCheckResult):
            return result
        if result is None:
            return FeatureCheckResult(
                True,
                f"{feature.display} check completed successfully.",
                messages.SUCCESS,
            )
        if isinstance(result, tuple) and len(result) >= 2:
            success, message, *rest = result
            level = (
                rest[0] if rest else (messages.SUCCESS if success else messages.ERROR)
            )
            return FeatureCheckResult(bool(success), str(message), int(level))
        if isinstance(result, bool):
            message = f"{feature.display} check {'passed' if result else 'failed'}."
            level = messages.SUCCESS if result else messages.ERROR
            return FeatureCheckResult(result, message, level)
        raise TypeError(f"Unsupported feature check result type: {type(result)!r}")


feature_checks = FeatureCheckRegistry()


@feature_checks.register("gpio-rtc")
def _check_gpio_rtc(feature: NodeFeature, node: Node | None):
    from .models import Node

    target: Node | None = node or Node.get_local()
    if target is None:
        return FeatureCheckResult(
            False,
            f"No local node is registered; cannot verify {feature.display}.",
            messages.WARNING,
        )
    if not has_clock_device():
        return FeatureCheckResult(
            False,
            f"No I2C clock device detected on {target.hostname} for {feature.display}.",
            messages.WARNING,
        )
    if target.has_feature("gpio-rtc"):
        return FeatureCheckResult(
            True,
            f"{feature.display} is enabled on {target.hostname} and an RTC is available.",
            messages.SUCCESS,
        )
    return FeatureCheckResult(
        True,
        f"{feature.display} is eligible on {target.hostname}; an RTC is available.",
        messages.INFO,
    )


@feature_checks.register_default
def _default_feature_check(
    feature: NodeFeature, node: Node | None
) -> FeatureCheckResult:
    from .models import Node

    target: Node | None = node or Node.get_local()
    if target is None:
        return FeatureCheckResult(
            False,
            f"No local node is registered; cannot verify {feature.display}.",
            messages.WARNING,
        )
    try:
        enabled = feature.is_enabled
    except Exception as exc:  # pragma: no cover - defensive
        return FeatureCheckResult(
            False,
            f"{feature.display} check failed: {exc}",
            messages.ERROR,
        )
    if enabled:
        return FeatureCheckResult(
            True,
            f"{feature.display} is enabled on {target.hostname}.",
            messages.SUCCESS,
        )
    return FeatureCheckResult(
        False,
        f"{feature.display} is not enabled on {target.hostname}.",
        messages.WARNING,
    )


__all__ = [
    "FeatureCheck",
    "FeatureCheckRegistry",
    "FeatureCheckResult",
    "feature_checks",
]
