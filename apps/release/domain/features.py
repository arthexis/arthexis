from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from django.utils.text import slugify


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class ReleaseFeature:
    """Describe a notable change that will ship in a release."""

    title: str
    summary: str
    category: str = "feature"
    ticket: str | None = None
    scope: str | None = None
    breaking: bool = False

    def __post_init__(self) -> None:
        title = (self.title or "").strip()
        summary = (self.summary or "").strip()
        if not title:
            raise ValueError("Feature title is required")
        if not summary:
            raise ValueError("Feature summary is required")

        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)

        category = (self.category or "feature").strip().lower() or "feature"
        object.__setattr__(self, "category", category)

        ticket = _clean_text(self.ticket)
        scope = _clean_text(self.scope)
        object.__setattr__(self, "ticket", ticket)
        object.__setattr__(self, "scope", scope)

    @property
    def slug(self) -> str:
        """Return a URL-safe identifier derived from the title."""

        slug = slugify(self.title)
        return slug or self.title

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ReleaseFeature":
        """Build a feature from a mapping such as JSON or YAML data."""

        title = str(data.get("title", ""))
        summary = str(data.get("summary", ""))
        category = str(data.get("category", "feature"))
        ticket = data.get("ticket")
        scope = data.get("scope")
        breaking = bool(data.get("breaking", False))
        ticket_value = str(ticket) if ticket is not None else None
        scope_value = str(scope) if scope is not None else None
        return cls(
            title=title,
            summary=summary,
            category=category,
            ticket=ticket_value,
            scope=scope_value,
            breaking=breaking,
        )

    def as_bullet(self) -> str:
        """Render the feature as a human-friendly bullet item."""

        label = self.category.capitalize()
        prefix = "BREAKING: " if self.breaking else ""
        ticket = f" [{self.ticket}]" if self.ticket else ""
        scope = f" ({self.scope})" if self.scope else ""
        return f"- {label}{scope}: {prefix}{self.title}{ticket} — {self.summary}"


@dataclass(frozen=True, slots=True)
class ReleaseFeatures:
    """Container for a collection of release features."""

    version: str | None
    features: tuple[ReleaseFeature, ...]

    def __post_init__(self) -> None:
        normalized = tuple(self.features)
        object.__setattr__(self, "features", normalized)

    def __iter__(self):
        return iter(self.features)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.features)

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return bool(self.features)

    @property
    def breaking_changes(self) -> tuple[ReleaseFeature, ...]:
        """Return only the features marked as breaking changes."""

        return tuple(feature for feature in self.features if feature.breaking)

    def format(self) -> str:
        """Return a newline-joined bullet list of all features."""

        return "\n".join(feature.as_bullet() for feature in self.features)

    @classmethod
    def from_iterable(
        cls, version: str | None, features: Iterable[Mapping[str, object] | ReleaseFeature]
    ) -> "ReleaseFeatures":
        parsed: list[ReleaseFeature] = []
        for item in features:
            if isinstance(item, ReleaseFeature):
                parsed.append(item)
            else:
                parsed.append(ReleaseFeature.from_mapping(item))
        return cls(version=version, features=tuple(parsed))

    def by_category(self, category: str) -> tuple[ReleaseFeature, ...]:
        """Return features filtered by normalized category."""

        normalized = (category or "").strip().lower()
        return tuple(
            feature for feature in self.features if feature.category == normalized
        )
