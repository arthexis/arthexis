from django.apps import AppConfig
from pathlib import Path
import base64


class GameConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "game"

    def ready(self):
        static_dir = Path(__file__).resolve().parent / "static"
        for b64_file in static_dir.rglob("*.b64"):
            target = b64_file.with_suffix("")
            if not target.exists():
                data = base64.b64decode(b64_file.read_bytes())
                target.write_bytes(data)
