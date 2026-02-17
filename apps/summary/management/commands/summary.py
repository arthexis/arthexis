from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.screens.lcd_screen import locks as lcd_locks
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
    read_lcd_lock_file,
)
from apps.summary.node_features import get_llm_summary_prereq_state
from apps.summary.models import LLMSummaryConfig
from apps.summary.services import ensure_local_model, get_summary_config, normalize_screens, parse_screens


class Command(BaseCommand):
    """Report LCD summarizer status and optionally auto-enable prerequisites."""

    REQUIRED_FEATURE_SLUGS = ("celery-queue", "lcd-screen", "llm-summary")

    help = "Show LCD summarizer status and the current summary LCD rotation plan."

    def add_arguments(self, parser) -> None:
        """Register command-line flags."""

        parser.add_argument(
            "--enabled",
            action="store_true",
            help="Enable required locks/features so LCD summary can run.",
        )
        parser.add_argument(
            "--run-now",
            action="store_true",
            help="Generate the LCD summary immediately before printing status.",
        )

    def handle(self, *args, **options) -> None:
        """Render status output and apply optional auto-enable actions."""

        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for this command.")

        config = get_summary_config()
        base_dir = Path(settings.BASE_DIR)
        base_path = node.get_base_path()

        if options["enabled"]:
            self._enable_prerequisites(node=node, config=config, base_dir=base_dir)

        if options["run_now"]:
            run_status = self._run_summary_task_now()
            self.stdout.write(f"Run now: {run_status}")

        prereqs = get_llm_summary_prereq_state(base_dir=base_dir, base_path=base_path)
        current_message = read_lcd_lock_file(base_dir / ".locks" / lcd_locks.LOW_LOCK_FILE.name)
        planned_screens = normalize_screens(parse_screens(config.last_output))
        current_pair = (
            (current_message.subject.strip(), current_message.body.strip())
            if current_message is not None
            else None
        )

        self.stdout.write(self.style.MIGRATE_HEADING("LCD Summary Status"))
        self.stdout.write(f"Node: {node.hostname} (id={node.pk})")
        self.stdout.write(
            "Feature assignments: "
            + self._feature_assignment_line(
                node,
                slugs=self.REQUIRED_FEATURE_SLUGS,
            )
        )
        self.stdout.write(f"Summary config active: {'yes' if config.is_active else 'no'}")
        self.stdout.write(
            f"Model path: {config.model_path or '(default)'}"
        )
        self.stdout.write(
            f"Installed at: {config.installed_at.isoformat() if config.installed_at else 'never'}"
        )
        self.stdout.write(
            f"Last run: {config.last_run_at.isoformat() if config.last_run_at else 'never'}"
        )
        self.stdout.write(
            "Prerequisites: "
            f"lcd={'ok' if prereqs['lcd_enabled'] else 'missing'}, "
            f"celery={'ok' if prereqs['celery_enabled'] else 'missing'}"
        )

        channel_plan = self._load_channel_plan(base_dir)
        self.stdout.write(f"Channel order: {', '.join(channel_plan) if channel_plan else '(default)'}")

        self.stdout.write(self.style.MIGRATE_HEADING("Summary Plan"))
        if not planned_screens:
            self.stdout.write("No summary plan captured yet. Run the summary task first.")
            return

        for index, (subject, body) in enumerate(planned_screens, start=1):
            marker = "*" if current_pair == (subject.strip(), body.strip()) else " "
            self.stdout.write(f"{marker} {index:02d}. {subject} | {body}")

    def _enable_prerequisites(
        self, *, node: Node, config: LLMSummaryConfig, base_dir: Path
    ) -> None:
        """Enable lock files, feature assignments, and model artifacts for summaries."""

        lock_dir = base_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "celery.lck").touch(exist_ok=True)
        (lock_dir / LCD_RUNTIME_LOCK_FILE).touch(exist_ok=True)
        ensure_local_model(config)
        config.is_active = True
        config.save(update_fields=["is_active", "model_path", "installed_at", "updated_at"])

        feature_displays = {
            "celery-queue": "Celery Queue",
            "lcd-screen": "LCD Screen",
            "llm-summary": "LLM Summary",
        }
        for slug in self.REQUIRED_FEATURE_SLUGS:
            display = feature_displays[slug]
            feature, _created = NodeFeature.objects.get_or_create(
                slug=slug,
                defaults={"display": display},
            )
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)

        self.stdout.write(self.style.SUCCESS("Enabled LCD summary prerequisites."))

    def _feature_assignment_line(self, node: Node, *, slugs: tuple[str, ...]) -> str:
        """Return a compact feature-assignment status string for the node."""

        assigned = set(node.features.filter(slug__in=slugs).values_list("slug", flat=True))
        return ", ".join(f"{slug}={'yes' if slug in assigned else 'no'}" for slug in slugs)

    def _load_channel_plan(self, base_dir: Path) -> list[str]:
        """Return configured LCD channel order from lock file if available."""

        channel_lock = base_dir / ".locks" / LCD_CHANNELS_LOCK_FILE
        try:
            raw = channel_lock.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []

        try:
            return lcd_locks.parse_channel_order(raw)
        except Exception:
            return []

    def _run_summary_task_now(self) -> str:
        """Execute the summary task inline and return the resulting status string."""

        from apps.tasks.tasks import generate_lcd_log_summary

        return generate_lcd_log_summary()
