from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.skills.models import AgentSkill
from apps.nodes.models import NodeRole

DEFAULT_SKILL_ROLE_MAP = {
    "cp-doctor": "Satellite",
    "arthexis-cleanup-step": "Control",
    "ui-preview-capture": "Control",
    "arthexis-end-step": "Control",
    "arthexis-lcd-attention": "Control",
    "django-change-safety": "Control",
}


def _restore_or_create_seed_skill(
    *,
    slug: str,
    title: str,
    markdown: str,
) -> tuple[AgentSkill, bool]:
    skill = AgentSkill.all_objects.filter(slug=slug).first()
    if skill is None:
        return (
            AgentSkill.objects.create(slug=slug, title=title, markdown=markdown),
            True,
        )
    skill.title = title
    skill.markdown = markdown
    skill.is_deleted = False
    skill.save(update_fields=["title", "markdown", "is_deleted"])
    return skill, False


def sync_filesystem_to_db() -> int:
    skills_root = Path(settings.BASE_DIR) / "skills"
    changed = 0
    with transaction.atomic():
        keep_slugs = set(DEFAULT_SKILL_ROLE_MAP)
        AgentSkill.objects.filter(is_seed_data=True).exclude(
            slug__in=keep_slugs
        ).delete()
        for slug, role_name in DEFAULT_SKILL_ROLE_MAP.items():
            path = skills_root / slug / "SKILL.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            title = slug.replace("-", " ").title()
            skill, created = _restore_or_create_seed_skill(
                slug=slug,
                title=title,
                markdown=content,
            )
            AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
            skill.is_seed_data = True
            role = NodeRole.objects.filter(name=role_name).first()
            if role:
                skill.node_roles.set([role])
            else:
                skill.node_roles.clear()
            changed += 1 if created else 0
    return changed


def sync_db_to_filesystem() -> int:
    skills_root = Path(settings.BASE_DIR) / "skills"
    keep = set(AgentSkill.objects.values_list("slug", flat=True))
    for child in skills_root.iterdir():
        if child.is_dir() and child.name not in keep:
            for nested in sorted(child.rglob("*"), reverse=True):
                if nested.is_file() or nested.is_symlink():
                    nested.unlink()
                elif nested.is_dir():
                    nested.rmdir()
            child.rmdir()

    updates = 0
    for skill in AgentSkill.objects.all():
        target = skills_root / skill.slug / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing != skill.markdown:
            target.write_text(skill.markdown, encoding="utf-8")
            updates += 1
    return updates
