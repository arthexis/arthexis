import hashlib
from pathlib import Path
from django.conf import settings
from django.core import checks


def _fixture_hash() -> str:
    base_dir = Path(settings.BASE_DIR)
    md5 = hashlib.md5()
    for path in sorted(base_dir.glob("**/fixtures/*.json")):
        md5.update(path.read_bytes())
    return md5.hexdigest()


@checks.register(checks.Tags.database)
def check_unapplied_fixtures(app_configs=None, **kwargs):
    """Warn if fixture files have changed since last refresh."""
    hash_file = Path(settings.BASE_DIR) / "fixtures.md5"
    stored = hash_file.read_text().strip() if hash_file.exists() else ""
    current = _fixture_hash()
    if stored != current:
        return [
            checks.Warning(
                "Unapplied fixture changes detected.",
                hint="Run env-refresh to apply fixtures.",
                id="core.W001",
            )
        ]
    return []
