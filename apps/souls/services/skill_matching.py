from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.skills.models import Skill
from apps.souls.models import AgentInterfaceSpec, SkillBundle, SoulIntent

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}", re.IGNORECASE)
EXACT_MATCH_THRESHOLD = 0.92


@dataclass(frozen=True)
class SkillMatchCandidate:
    skill_id: int
    slug: str
    title: str
    score: float
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_intent_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").strip().split()).lower()


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(value or "")}


def _skill_search_text(skill: Skill) -> str:
    parts = [skill.slug, skill.title, skill.description, skill.markdown]
    package_files = skill.package_files.all()
    for package_file in package_files:
        if package_file.included_by_default:
            parts.append(package_file.relative_path)
            parts.append(package_file.content)
    return "\n".join(parts)


def _score_skill(
    skill: Skill, prompt: str, prompt_tokens: set[str]
) -> SkillMatchCandidate:
    search_text = _skill_search_text(skill).lower()
    slug = skill.slug.lower()
    title = skill.title.lower()
    reasons: list[str] = []
    score = 0.0

    if prompt == slug or prompt == title:
        score += 1.0
        reasons.append("exact slug/title")
    elif prompt and (prompt in slug or prompt in title):
        score += 0.72
        reasons.append("slug/title contains prompt")

    if prompt and prompt in search_text:
        score += 0.35
        reasons.append("content phrase")

    skill_tokens = _tokens(search_text)
    if prompt_tokens:
        overlap = prompt_tokens & skill_tokens
        coverage = len(overlap) / len(prompt_tokens)
        if coverage:
            score += min(0.8, coverage)
            reasons.append(f"token overlap {len(overlap)}/{len(prompt_tokens)}")

    return SkillMatchCandidate(
        skill_id=skill.pk,
        slug=skill.slug,
        title=skill.title,
        score=round(min(score, 1.0), 4),
        reasons=reasons,
    )


def _validate_limit(limit: int) -> int:
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as error:
        raise ValueError("limit must be an integer.") from error
    if normalized_limit < 0:
        raise ValueError("limit must be non-negative.")
    return normalized_limit


def search_skills(prompt: str, *, limit: int = 10) -> list[SkillMatchCandidate]:
    limit = _validate_limit(limit)
    normalized = normalize_intent_prompt(prompt)
    prompt_tokens = _tokens(normalized)
    matches = [
        _score_skill(skill, normalized, prompt_tokens)
        for skill in Skill.objects.prefetch_related("package_files").order_by("slug")
    ]
    matches = [match for match in matches if match.score > 0]
    matches.sort(key=lambda match: (-match.score, match.slug))
    return matches[:limit]


def _default_interface_schema(prompt: str, matches: list[SkillMatchCandidate]) -> dict:
    return {
        "schema_version": "soul_seed.interface.v1",
        "intent": prompt,
        "primary_match": matches[0].to_dict() if matches else None,
        "allowed_surfaces": ["cli", "web"],
    }


def compose_skill_bundle(
    prompt: str,
    *,
    created_by=None,
    limit: int = 5,
    dry_run: bool = True,
) -> dict:
    normalized = normalize_intent_prompt(prompt)
    matches = search_skills(normalized, limit=limit)
    primary_match = matches[0] if matches else None
    strategy = (
        SkillBundle.MatchStrategy.EXACT
        if primary_match and primary_match.score >= EXACT_MATCH_THRESHOLD
        else SkillBundle.MatchStrategy.COMPOSED
    )
    summary = (
        f"Exact skill match for: {normalized}"
        if strategy == SkillBundle.MatchStrategy.EXACT
        else f"Composed bundle for: {normalized}"
    )
    result = {
        "dry_run": dry_run,
        "intent": {
            "problem_statement": prompt,
            "normalized_intent": normalized,
        },
        "matches": [match.to_dict() for match in matches],
        "bundle": {
            "name": normalized[:120] or "Soul Seed Bundle",
            "slug": slugify(normalized)[:100] or "soul-seed-bundle",
            "match_strategy": strategy,
            "match_score": primary_match.score if primary_match else 0.0,
            "primary_skill": primary_match.slug if primary_match else "",
            "skill_slugs": [match.slug for match in matches],
            "summary": summary,
        },
        "interface_spec": {
            "mode": AgentInterfaceSpec.Mode.AUTO,
            "schema": _default_interface_schema(normalized, matches),
            "commands": ["suggest_next_action", "show_context"],
            "suggestions": [
                _("Ask for the missing input before taking action."),
                _(
                    "Prefer the highest-scoring registered skill before composing a workflow."
                ),
            ],
            "visible_fields": ["intent", "matches", "commands", "suggestions"],
        },
    }
    if dry_run:
        return result

    user_model = get_user_model()
    if created_by is not None and not isinstance(created_by, user_model):
        created_by = None
    with transaction.atomic():
        intent = SoulIntent.objects.create(
            problem_statement=prompt,
            normalized_intent=normalized,
            created_by=created_by,
        )
        base_slug = result["bundle"]["slug"]
        bundle_slug = base_slug
        suffix = 1
        while SkillBundle.objects.filter(slug=bundle_slug).exists():
            suffix += 1
            bundle_slug = f"{base_slug[:112]}-{suffix}"
        primary_skill = (
            Skill.objects.filter(pk=primary_match.skill_id).first()
            if primary_match
            else None
        )
        bundle = SkillBundle.objects.create(
            name=result["bundle"]["name"],
            slug=bundle_slug,
            intent=intent,
            primary_skill=primary_skill,
            match_strategy=strategy,
            match_score=result["bundle"]["match_score"],
            summary=summary,
            fallback_guidance=_(
                "If no exact workflow fits, ask the operator to clarify scope before acting."
            ),
        )
        if matches:
            skills = Skill.objects.filter(pk__in=[match.skill_id for match in matches])
            bundle.skills.set(skills)
        spec = AgentInterfaceSpec.objects.create(
            bundle=bundle,
            mode=AgentInterfaceSpec.Mode.AUTO,
            schema=result["interface_spec"]["schema"],
            commands=result["interface_spec"]["commands"],
            suggestions=result["interface_spec"]["suggestions"],
            visible_fields=result["interface_spec"]["visible_fields"],
        )
    result["intent"]["id"] = intent.pk
    result["bundle"]["id"] = bundle.pk
    result["bundle"]["slug"] = bundle.slug
    result["interface_spec"]["id"] = spec.pk
    return result
