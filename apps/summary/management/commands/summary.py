from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.features.utils import is_suite_feature_enabled
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.nodes.roles import node_is_control
from apps.screens.lcd_screen import locks as lcd_locks
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
    LCD_SUMMARY_LOCK_FILE,
    read_lcd_lock_file,
)
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.dense_lcd import execute_dense_lcd_summary
from apps.summary.models import LLMSummaryConfig
from apps.summary.node_features import get_llm_summary_prereq_state
from apps.summary.services import (
    execute_log_summary_generation,
    get_summary_config,
    normalize_screens,
    parse_screens,
    resolve_summary_output_file_path,
    summary_output_target,
)


class Command(BaseCommand):
    """Report deterministic summarizer status and optionally enable LCD output."""

    LCD_FEATURE_SLUGS = ("celery-queue", "lcd-screen", "llm-summary")
    FILE_FEATURE_SLUGS = ("celery-queue", "llm-summary")

    help = "Show deterministic summarizer status and the current summary rotation plan."

    def add_arguments(self, parser) -> None:
        """Register command-line flags."""

        parser.add_argument(
            "--enabled",
            action="store_true",
            help="Enable LCD/Celery locks and node features for summary generation and display.",
        )
        parser.add_argument(
            "--run-now",
            action="store_true",
            help="Generate the LCD summary immediately before printing status.",
        )
        parser.add_argument(
            "--allow-disabled-feature",
            action="store_true",
            help=(
                f"Allow manual --run-now execution even when {LLM_SUMMARY_SUITE_FEATURE_SLUG} suite "
                "feature is disabled."
            ),
        )
        parser.add_argument(
            "--dense-lcd",
            action="store_true",
            help="Generate dense low-channel LCD frames and exit.",
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

        if options["dense_lcd"]:
            status = execute_dense_lcd_summary(
                ignore_suite_feature_gate=options["allow_disabled_feature"],
            )
            self.stdout.write(f"Dense LCD: {status}")
            return

        if options["run_now"]:
            if not is_suite_feature_enabled(
                LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True
            ):
                if options["allow_disabled_feature"]:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Suite feature '{LLM_SUMMARY_SUITE_FEATURE_SLUG}' is disabled; "
                            "running manual override via --allow-disabled-feature."
                        )
                    )
                    run_status = self._run_summary_task_now(
                        ignore_suite_feature_gate=True
                    )
                else:
                    run_status = "skipped:suite-feature-disabled"
                    self.stdout.write(
                        self.style.WARNING(
                            f"Suite feature '{LLM_SUMMARY_SUITE_FEATURE_SLUG}' is disabled; "
                            "skipping automated summary run. Re-run with "
                            "--allow-disabled-feature for one-off operator execution."
                        )
                    )
            else:
                run_status = self._run_summary_task_now()
            self.stdout.write(f"Run now: {run_status}")
            # The task updates summary config fields on its own model instance,
            # so reload to report the run that just completed.
            config = get_summary_config()

        prereqs = get_llm_summary_prereq_state(base_dir=base_dir, base_path=base_path)
        current_message = read_lcd_lock_file(
            base_dir / ".locks" / LCD_SUMMARY_LOCK_FILE
        )
        if (
            current_message is not None
            and current_message.expires_at is not None
            and current_message.expires_at <= timezone.now()
        ):
            current_message = None
        planned_screens = normalize_screens(parse_screens(config.last_output))
        current_pair = (
            (current_message.subject.strip(), current_message.body.strip())
            if current_message is not None
            else None
        )

        self.stdout.write(self.style.MIGRATE_HEADING("Deterministic Summary Status"))
        self.stdout.write(f"Node: {node.hostname} (id={node.pk})")
        self.stdout.write(
            "Feature assignments: "
            + self._feature_assignment_line(
                node,
                slugs=self._required_feature_slugs(config),
            )
        )
        self.stdout.write(
            f"Summary config active: {'yes' if config.is_active else 'no'}"
        )
        self.stdout.write("Mode: Deterministic built-in summarizer")
        self.stdout.write(
            f"Last run: {config.last_run_at.isoformat() if config.last_run_at else 'never'}"
        )
        output_target = summary_output_target(config)
        try:
            output_file_path = str(
                resolve_summary_output_file_path(
                    config,
                    base_dir=base_dir,
                )
            )
        except ValueError as exc:
            output_file_path = f"invalid ({exc})"
        self.stdout.write(f"Output target: {output_target}")
        self.stdout.write(f"Configured file path: {output_file_path}")
        self.stdout.write(
            f"Last output file: {config.last_output_file_path or 'never'}"
        )
        self.stdout.write(
            "Output/schedule state: "
            f"lcd={self._lcd_state_for_target(config, prereqs=prereqs)}, "
            f"celery={'ok' if prereqs['celery_enabled'] else 'missing'}"
        )

        channel_plan = self._load_channel_plan(base_dir)
        self.stdout.write(
            f"Channel order: {', '.join(channel_plan) if channel_plan else '(default)'}"
        )

        self.stdout.write(self.style.MIGRATE_HEADING("Summary Plan"))
        if not planned_screens:
            self.stdout.write(
                "No summary plan captured yet. Run the summary task first."
            )
            return

        for index, (subject, body) in enumerate(planned_screens, start=1):
            marker = "*" if current_pair == (subject.strip(), body.strip()) else " "
            self.stdout.write(f"{marker} {index:02d}. {subject} | {body}")

    def _enable_prerequisites(
        self, *, node: Node, config: LLMSummaryConfig, base_dir: Path
    ) -> None:
        """Enable lock files and feature assignments for summaries."""

        if not node_is_control(node):
            raise CommandError(
                "Deterministic summary can only be enabled on Control nodes."
            )

        lock_dir = base_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "celery.lck").touch(exist_ok=True)
        if summary_output_target(config) == LLMSummaryConfig.OutputTarget.LCD:
            (lock_dir / LCD_RUNTIME_LOCK_FILE).touch(exist_ok=True)
        config.is_active = True
        config.save(update_fields=["is_active", "updated_at"])

        feature_displays = {
            "celery-queue": "Celery Queue",
            "lcd-screen": "LCD Screen",
            "llm-summary": "Deterministic Summary",
        }
        for slug in self._required_feature_slugs(config):
            display = feature_displays[slug]
            feature, _created = NodeFeature.objects.get_or_create(
                slug=slug,
                defaults={"display": display},
            )
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)

        self.stdout.write(self.style.SUCCESS("Enabled summary prerequisites."))

    def _required_feature_slugs(self, config: LLMSummaryConfig) -> tuple[str, ...]:
        """Return the feature set required for the configured output target."""

        if summary_output_target(config) == LLMSummaryConfig.OutputTarget.FILE:
            return self.FILE_FEATURE_SLUGS
        return self.LCD_FEATURE_SLUGS

    def _lcd_state_for_target(
        self,
        config: LLMSummaryConfig,
        *,
        prereqs: dict[str, bool],
    ) -> str:
        """Return LCD readiness text appropriate for the active target."""

        if summary_output_target(config) == LLMSummaryConfig.OutputTarget.FILE:
            return "not-required"
        return "ok" if prereqs["lcd_enabled"] else "missing"

    def _feature_assignment_line(self, node: Node, *, slugs: tuple[str, ...]) -> str:
        """Return a compact feature-assignment status string for the node."""

        assigned = set(
            node.features.filter(slug__in=slugs).values_list("slug", flat=True)
        )
        return ", ".join(
            f"{slug}={'yes' if slug in assigned else 'no'}" for slug in slugs
        )

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

    def _run_summary_task_now(self, *, ignore_suite_feature_gate: bool = False) -> str:
        """Execute the summary task inline and return the resulting status string."""
        return execute_log_summary_generation(
            ignore_suite_feature_gate=ignore_suite_feature_gate,
        )
