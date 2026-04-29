from pathlib import Path

from django.conf import settings
from django.db import migrations

SKILL_ROLES = {
    "cp-doctor": "Satellite",
    "arthexis-cleanup-step": "Control",
    "ui-preview-capture": "Control",
    "arthexis-end-step": "Control",
    "arthexis-lcd-attention": "Control",
    "django-change-safety": "Control",
}


def seed_agent_skills(apps, schema_editor):
    del schema_editor
    AgentSkill = apps.get_model("skills", "AgentSkill")
    NodeRole = apps.get_model("nodes", "NodeRole")
    skills_root = Path(settings.BASE_DIR) / "skills"

    AgentSkill.objects.exclude(slug__in=set(SKILL_ROLES)).delete()

    for slug, role_name in SKILL_ROLES.items():
        path = skills_root / slug / "SKILL.md"
        if not path.exists():
            continue
        skill, _ = AgentSkill.objects.update_or_create(
            slug=slug,
            defaults={
                "title": slug.replace("-", " ").title(),
                "markdown": path.read_text(encoding="utf-8"),
            },
        )
        role = NodeRole.objects.filter(name=role_name).first()
        if role:
            skill.node_roles.set([role])


class Migration(migrations.Migration):
    dependencies = [("skills", "0001_initial")]

    operations = [migrations.RunPython(seed_agent_skills, migrations.RunPython.noop)]
