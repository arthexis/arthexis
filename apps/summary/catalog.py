from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SummaryModelSpec:
    """Built-in model catalog entry for LCD summaries."""

    slug: str
    display: str
    family: str
    runtime_backend: str
    hf_repo: str
    context_window: int
    recommended: bool = False
    notes: str = ""


SUMMARY_MODEL_SPECS: tuple[SummaryModelSpec, ...] = (
    SummaryModelSpec(
        slug="gemma-4-e2b-it",
        display="Gemma 4 E2B IT",
        family="gemma-4",
        runtime_backend="llama_cpp_server",
        hf_repo="ggml-org/gemma-4-E2B-it-GGUF",
        context_window=128_000,
        recommended=True,
        notes="Smallest Gemma 4 option and the safest default for LCD log summaries.",
    ),
    SummaryModelSpec(
        slug="gemma-4-e4b-it",
        display="Gemma 4 E4B IT",
        family="gemma-4",
        runtime_backend="llama_cpp_server",
        hf_repo="ggml-org/gemma-4-E4B-it-GGUF",
        context_window=128_000,
        notes="Higher quality than E2B with a larger memory footprint.",
    ),
    SummaryModelSpec(
        slug="gemma-4-26b-a4b-it",
        display="Gemma 4 26B A4B IT",
        family="gemma-4",
        runtime_backend="llama_cpp_server",
        hf_repo="ggml-org/gemma-4-26B-A4B-it-GGUF",
        context_window=256_000,
        notes="Mixture-of-experts variant for stronger reasoning when workstation-class hardware is available.",
    ),
    SummaryModelSpec(
        slug="gemma-4-31b-it",
        display="Gemma 4 31B IT",
        family="gemma-4",
        runtime_backend="llama_cpp_server",
        hf_repo="ggml-org/gemma-4-31B-it-GGUF",
        context_window=256_000,
        notes="Largest Gemma 4 option; generally too heavy for Raspberry Pi-class deployments.",
    ),
)

SUMMARY_MODEL_SPEC_MAP = {spec.slug: spec for spec in SUMMARY_MODEL_SPECS}

SUMMARY_MODEL_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (spec.slug, spec.display) for spec in SUMMARY_MODEL_SPECS
)


def get_summary_model_spec(slug: str) -> SummaryModelSpec | None:
    """Return one catalog entry by slug."""

    return SUMMARY_MODEL_SPEC_MAP.get((slug or "").strip())


__all__ = [
    "SUMMARY_MODEL_CHOICES",
    "SUMMARY_MODEL_SPECS",
    "SummaryModelSpec",
    "get_summary_model_spec",
]
