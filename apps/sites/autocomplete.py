from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import re

from django.conf import settings

from apps.tasks.tasks import LocalLLMSummarizer

TOKEN_RE = re.compile(r"[a-zA-Z0-9_/-]{2,}")

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

    def __init__(self) -> None:
        self._deterministic = LocalLLMSummarizer()

    def suggest(self, *, text: str, is_staff: bool, limit: int = 5) -> list[str]:
        cleaned = (text or "").strip()
        if is_staff:
            return self._repo_trained_suggestions(text=cleaned, limit=limit)
        return self._standard_suggestions(text=cleaned, limit=limit)

    def _standard_suggestions(self, *, text: str, limit: int) -> list[str]:
        tail = text.split()[-1].lower() if text.split() else ""
        suggestions: list[str] = []
        if tail:
            for phrase in STANDARD_FEEDBACK_PHRASES:
                for token in phrase.split():
                    normalized = token.strip(".,;:!?()").lower()
                    if normalized.startswith(tail) and normalized != tail:
                        suggestions.append(token.strip(".,;:!?()"))
                if len(suggestions) >= limit:
                    break
        if len(suggestions) < limit:
            prompt = self._build_feedback_prompt(text=text)
            generated = self._deterministic.summarize(prompt)
            for line in generated.splitlines():
                candidate = line.strip(" -")
                if candidate and candidate not in suggestions:
                    suggestions.append(candidate)
                if len(suggestions) >= limit:
                    break
        return suggestions[:limit]

    def _repo_trained_suggestions(self, *, text: str, limit: int) -> list[str]:
        tokens = [token.lower() for token in TOKEN_RE.findall(text)]
        model = _repo_token_model()
        suggestions: list[str] = []
        if tokens:
            previous = tokens[-1]
            for candidate in model.get(previous, []):
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

    def _build_feedback_prompt(self, *, text: str) -> str:
        compact_input = text[:200]
        return f"Summarize feedback context for suggestions.\\nLOGS:\\n{compact_input}"


@lru_cache(maxsize=1)
def _repo_token_model() -> dict[str, list[str]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for token_stream in _iter_repo_token_streams():
        previous = None
        for token in token_stream:
            if previous is not None:
                counts[previous][token] += 1
            previous = token
    return {
        token: [candidate for candidate, _ in counter.most_common(10)]
        for token, counter in counts.items()
    }


@lru_cache(maxsize=1)
def _repo_common_tokens() -> list[str]:
    counter: Counter[str] = Counter()
    for token_stream in _iter_repo_token_streams():
        counter.update(token_stream)
    return [token for token, _ in counter.most_common(10)]


def _iter_repo_token_streams():
    base_dir = Path(settings.BASE_DIR)
    include_suffixes = {".py", ".md", ".html", ".js"}
    for path in base_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in include_suffixes:
            continue
        if "/.venv/" in str(path) or "/node_modules/" in str(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tokens = [token.lower() for token in TOKEN_RE.findall(content)]
        if tokens:
            yield tokens
