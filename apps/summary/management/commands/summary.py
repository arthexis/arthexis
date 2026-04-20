from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.summary.catalog import SUMMARY_MODEL_SPECS, get_summary_model_spec
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.screens.lcd_screen import locks as lcd_locks
from apps.screens.startup_notifications import (
    LCD_CHANNELS_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
    read_lcd_lock_file,
)
from apps.summary.node_features import get_llm_summary_prereq_state
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.models import LLMSummaryConfig
from apps.summary.services import (
    build_summary_runtime_launch_plan,
    execute_log_summary_generation,
    get_selected_summary_model,
    get_summary_config,
    launch_summary_runtime_server,
    normalize_screens,
    parse_screens,
    probe_summary_runtime,
    resolve_runtime_base_url,
    resolve_runtime_binary_path,
    sync_summary_suite_feature,
    summary_runtime_service_lock_enabled,
    summary_runtime_is_ready,
)


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
        parser.add_argument(
            "--allow-disabled-feature",
            action="store_true",
            help=(
                f"Allow manual --run-now execution even when {LLM_SUMMARY_SUITE_FEATURE_SLUG} suite "
                "feature is disabled."
            ),
        )
        parser.add_argument(
            "--list-models",
            action="store_true",
            help="List the built-in summary model catalog and exit.",
        )
        parser.add_argument(
            "--select-model",
            help="Select one built-in summary model by slug and persist it on the local config.",
        )
        parser.add_argument(
            "--runtime-base-url",
            help="Set the local OpenAI-compatible llama.cpp runtime base URL.",
        )
        parser.add_argument(
            "--probe-runtime",
            action="store_true",
            help="Probe the selected runtime now and persist the resolved model binding.",
        )
        parser.add_argument(
            "--runtime-binary-path",
            help="Set the local llama.cpp server binary path or command name.",
        )
        parser.add_argument(
            "--print-runtime-command",
            action="store_true",
            help="Print the managed local runtime launch command and exit.",
        )
        parser.add_argument(
            "--serve-runtime",
            action="store_true",
            help="Run the managed local llama.cpp runtime service in the foreground.",
        )

    def handle(self, *args, **options) -> None:
        """Render status output and apply optional auto-enable actions."""

        if options["list_models"]:
            self._write_model_catalog()
            return
        if options["serve_runtime"]:
            self._serve_runtime()
            return

        config = get_summary_config()
        if (
            options["select_model"]
            or options["runtime_base_url"]
            or options["runtime_binary_path"]
        ):
            self._update_runtime_settings(
                config=config,
                selected_model=options.get("select_model"),
                runtime_base_url=options.get("runtime_base_url"),
                runtime_binary_path=options.get("runtime_binary_path"),
                probe_runtime=options["probe_runtime"],
            )
            config = get_summary_config()
        elif options["probe_runtime"]:
            runtime_state = probe_summary_runtime(config)
            sync_summary_suite_feature(config)
            level = self.style.SUCCESS if runtime_state.ready else self.style.WARNING
            self.stdout.write(level(runtime_state.detail))
            config = get_summary_config()

        if options["print_runtime_command"]:
            self._write_runtime_command(config)
            return

        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for this command.")

        base_dir = Path(settings.BASE_DIR)
        base_path = node.get_base_path()

        if options["enabled"]:
            self._enable_prerequisites(node=node, config=config, base_dir=base_dir)
            config = get_summary_config()

        if options["run_now"]:
            if not is_suite_feature_enabled(LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True):
                if options["allow_disabled_feature"]:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Suite feature '{LLM_SUMMARY_SUITE_FEATURE_SLUG}' is disabled; "
                            "running manual override via --allow-disabled-feature."
                        )
                    )
                    run_status = self._run_summary_task_now(ignore_suite_feature_gate=True)
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
        selected_model = get_selected_summary_model(config)
        self.stdout.write(
            f"Selected model: {selected_model.display if selected_model else 'none'}"
        )
        self.stdout.write(
            f"Model path: {config.model_path or '(default)'}"
        )
        self.stdout.write(f"Runtime base URL: {resolve_runtime_base_url(config)}")
        self.stdout.write(f"Runtime binary: {resolve_runtime_binary_path(config)}")
        self.stdout.write(
            f"Runtime model ID: {config.runtime_model_id or '(unresolved)'}"
        )
        self.stdout.write(
            f"Runtime ready: {'yes' if summary_runtime_is_ready(config) else 'no'}"
        )
        self.stdout.write(
            "Runtime service lock: "
            f"{'present' if summary_runtime_service_lock_enabled(base_dir=base_dir) else 'missing'}"
        )
        try:
            runtime_command = build_summary_runtime_launch_plan(config).audit_command
        except ValueError:
            runtime_command = config.model_command_audit or "(unavailable)"
        self.stdout.write(f"Runtime launch: {runtime_command}")
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
        config.is_active = True
        config.save(update_fields=["is_active", "updated_at"])

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

        sync_summary_suite_feature(config)
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

    def _run_summary_task_now(self, *, ignore_suite_feature_gate: bool = False) -> str:
        """Execute the summary task inline and return the resulting status string."""
        return execute_log_summary_generation(
            ignore_suite_feature_gate=ignore_suite_feature_gate,
        )

    def _write_model_catalog(self) -> None:
        """Render the built-in model catalog."""

        self.stdout.write(self.style.MIGRATE_HEADING("Summary Model Catalog"))
        for spec in SUMMARY_MODEL_SPECS:
            suffix = " [recommended]" if spec.recommended else ""
            self.stdout.write(
                f"{spec.slug}: {spec.display}{suffix} | family={spec.family} | "
                f"backend={spec.runtime_backend} | hf_repo={spec.hf_repo} | "
                f"context={spec.context_window}"
            )

    def _update_runtime_settings(
        self,
        *,
        config: LLMSummaryConfig,
        selected_model: str | None,
        runtime_base_url: str | None,
        runtime_binary_path: str | None,
        probe_runtime: bool,
    ) -> None:
        """Persist model/runtime settings and optionally probe the runtime."""

        update_fields = {"updated_at"}
        if selected_model is not None:
            spec = get_summary_model_spec(selected_model)
            if spec is None:
                raise CommandError(
                    f"Unknown summary model '{selected_model}'. Use --list-models to inspect valid choices."
                )
            config.selected_model = spec.slug
            config.backend = LLMSummaryConfig.Backend.LLAMA_CPP_SERVER
            config.runtime_model_id = ""
            config.runtime_is_ready = False
            config.last_runtime_error = ""
            update_fields.update(
                {
                    "selected_model",
                    "backend",
                    "runtime_model_id",
                    "runtime_is_ready",
                    "last_runtime_error",
                }
            )
        if runtime_base_url is not None:
            config.runtime_base_url = str(runtime_base_url).strip()
            config.runtime_model_id = ""
            config.runtime_is_ready = False
            config.last_runtime_error = ""
            update_fields.update(
                {
                    "runtime_base_url",
                    "runtime_model_id",
                    "runtime_is_ready",
                    "last_runtime_error",
                }
            )
        if runtime_binary_path is not None:
            config.runtime_binary_path = str(runtime_binary_path).strip()
            update_fields.add("runtime_binary_path")
        config.save(update_fields=sorted(update_fields))

        if probe_runtime:
            runtime_state = probe_summary_runtime(config)
            level = self.style.SUCCESS if runtime_state.ready else self.style.WARNING
            self.stdout.write(level(runtime_state.detail))

        sync_summary_suite_feature(config)

    def _write_runtime_command(self, config: LLMSummaryConfig) -> None:
        """Print the managed runtime launch command or a concrete configuration error."""

        try:
            plan = build_summary_runtime_launch_plan(config)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(plan.audit_command)

    def _serve_runtime(self) -> None:
        """Run the configured local summary runtime in the foreground."""

        config = get_summary_config()
        try:
            launch_summary_runtime_server(config)
        except (RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
