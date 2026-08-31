from __future__ import annotations

from pathlib import Path

from django.conf import settings

from apps.skills.models import Skill
from apps.skills.package_services import materialize_codex_skill_files

DEFAULT_SKILL_ROLE_MAP: dict[str, str] = {}


def sync_filesystem_to_db() -> int:
    Skill.objects.filter(is_seed_data=True).delete()
    return 0


def sync_db_to_filesystem() -> int:
    skills_root = Path(settings.BASE_DIR) / "skills"
    summary = materialize_codex_skill_files(skills_root)
    return summary["files_written"]
