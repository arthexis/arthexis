from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from django.conf import settings

TOKEN_RE = re.compile(r"[a-zA-Z0-9_/-]{2,}")
INPUT_TOKEN_RE = re.compile(r"[a-zA-Z0-9_/-]+")

STANDARD_FEEDBACK_PHRASES: tuple[str, ...] = (
    "The page loaded quickly and worked as expected",
    "I could not find the action I needed",
    "Please improve the clarity of this workflow",
    "The labels were clear and easy to follow",
    "I expected this action to show validation feedback",
    "This step should include a clearer status message",
    "The form submission failed without enough detail",
    "I would like a shortcut for this frequent action",
)


class FeedbackAutocompleteHarness:
    """Autocomplete harness for user and staff feedback dialogs."""

    def suggest(self, *, text: str, is_staff: bool, limit: int = 5) -> list[str]:
        if is_staff:
            return self._repo_trained_suggestions(text=text or "", limit=limit)
        return self._standard_suggestions(text=text or "", limit=limit)

    def _standard_suggestions(self, *, text: str, limit: int) -> list[str]:
        words = [word.strip(".,;:!?()").lower() for word in text.split()]
        has_trailing_space = bool(text) and text[-1].isspace()
        prefix = words if has_trailing_space else words[:-1]
        active = "" if has_trailing_space or not words else words[-1]
        suggestions: list[str] = []
        for phrase in STANDARD_FEEDBACK_PHRASES:
            phrase_words = [word.strip(".,;:!?()") for word in phrase.split()]
            normalized_phrase = [word.lower() for word in phrase_words]
            if len(prefix) > len(normalized_phrase):
                continue
            if normalized_phrase[: len(prefix)] != prefix:
                continue
            candidate_index = len(prefix)
            if candidate_index >= len(phrase_words):
                continue
            if active:
                candidate = normalized_phrase[candidate_index]
                if candidate == active:
                    candidate_index += 1
                    if candidate_index >= len(phrase_words):
                        continue
                elif not candidate.startswith(active):
                    continue
            suggestion = phrase_words[candidate_index]
            if suggestion not in suggestions:
                suggestions.append(suggestion)
            if len(suggestions) >= limit:
                break
        return suggestions[:limit]

    def _repo_trained_suggestions(self, *, text: str, limit: int) -> list[str]:
        tokens = [token.lower() for token in INPUT_TOKEN_RE.findall(text)]
        has_trailing_space = bool(text) and text[-1].isspace()
        previous = tokens[-1] if has_trailing_space and tokens else ""
        active = "" if has_trailing_space or not tokens else tokens[-1]
        if not has_trailing_space and len(tokens) > 1:
            previous = tokens[-2]
        model = _repo_token_model()
        suggestions: list[str] = []
        if previous:
            for candidate in model.get(previous, []):
                if active and not candidate.startswith(active):
                    continue
                if candidate not in suggestions:
                    suggestions.append(candidate)
                if len(suggestions) >= limit:
                    return suggestions
        for fallback in _repo_common_tokens():
            if fallback not in suggestions:
                suggestions.append(fallback)
            if len(suggestions) >= limit:
                break
        return suggestions

@lru_cache(maxsize=1)
def _repo_stats() -> tuple[dict[str, list[str]], list[str]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    counter: Counter[str] = Counter()
    for token_stream in _iter_repo_token_streams():
        previous = None
        for token in token_stream:
            counter[token] += 1
            if previous is not None:
                counts[previous][token] += 1
            previous = token
    model = {
        token: [candidate for candidate, _ in counter.most_common(10)]
        for token, counter in counts.items()
    }
    common = [token for token, _ in counter.most_common(10)]
    return model, common


def _repo_token_model() -> dict[str, list[str]]:
    return _repo_stats()[0]


def _repo_common_tokens() -> list[str]:
    return _repo_stats()[1]


def _iter_repo_token_streams():
    base_dir = Path(settings.BASE_DIR)
    include_suffixes = {".py", ".md", ".html", ".js"}
    exclude_dirs = {".git", ".venv", "node_modules"}
    for directory_name, names, files in os.walk(base_dir, onerror=lambda error: None):
        names[:] = [name for name in names if name not in exclude_dirs]
        directory = Path(directory_name)
        for file_name in files:
            path = directory / file_name
            if path.suffix.lower() not in include_suffixes:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            tokens = [token.lower() for token in TOKEN_RE.findall(content)]
            if tokens:
                yield tokens
