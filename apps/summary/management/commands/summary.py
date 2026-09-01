from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.features.utils import is_suite_feature_enabled
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.nodes.roles import node_is_control
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.models import LLMSummaryConfig
from apps.summary.node_features import get_llm_summary_prereq_state
from apps.summary.services import (
    execute_log_summary_generation,
    get_summary_config,
    resolve_summary_output_file_path,
    summary_output_target,
)


class Command(BaseCommand):
    """Report and operate the retained deterministic file summary."""

    help = "Show deterministic file-summary status and optionally run it."
    FEATURE_SLUGS = ("celery-queue", "llm-summary")

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--enabled",
            action="store_true",
            help="Enable Celery and the summary feature for file generation.",
        )
        parser.add_argument(
            "--run-now",
            action="store_true",
            help="Generate the file summary before printing status.",
        )
        parser.add_argument(
            "--allow-disabled-feature",
            action="store_true",
            help="Allow a manual run when the summary suite feature is disabled.",
        )

    def handle(self, *args, **options) -> None:
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for this command.")

        config = get_summary_config()
        base_dir = Path(settings.BASE_DIR)
        if options["enabled"]:
            self._enable_prerequisites(node=node, config=config, base_dir=base_dir)
        if options["run_now"]:
            allowed = is_suite_feature_enabled(LLM_SUMMARY_SUITE_FEATURE_SLUG, default=True)
            if not allowed and not options["allow_disabled_feature"]:
                run_status = "skipped:suite-feature-disabled"
            else:
                run_status = execute_log_summary_generation(
                    ignore_suite_feature_gate=not allowed
                )
            self.stdout.write(f"Run now: {run_status}")
            config = get_summary_config()

        try:
            output_path = resolve_summary_output_file_path(config, base_dir=base_dir)
        except ValueError as exc:
            output_path = f"invalid ({exc})"
        prereqs = get_llm_summary_prereq_state(
            base_dir=base_dir, base_path=node.get_base_path()
        )
        self.stdout.write(self.style.MIGRATE_HEADING("Deterministic Summary Status"))
        self.stdout.write(f"Node: {node.hostname} (id={node.pk})")
        self.stdout.write(f"Summary config active: {'yes' if config.is_active else 'no'}")
        self.stdout.write("Mode: Deterministic built-in summarizer")
        self.stdout.write(f"Output target: {summary_output_target(config)}")
        self.stdout.write(f"Configured file path: {output_path}")
        self.stdout.write(f"Last output file: {config.last_output_file_path or 'never'}")
        self.stdout.write(
            f"Celery lock: {'ok' if prereqs['celery_enabled'] else 'missing'}"
        )

    def _enable_prerequisites(
        self, *, node: Node, config: LLMSummaryConfig, base_dir: Path
    ) -> None:
        if not node_is_control(node):
            raise CommandError("Deterministic summary can only be enabled on Control nodes.")
        lock_dir = base_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "celery.lck").touch(exist_ok=True)
        config.is_active = True
        config.save(update_fields=["is_active", "updated_at"])
        for slug, display in (("celery-queue", "Celery Queue"), ("llm-summary", "Deterministic Summary")):
            feature, _created = NodeFeature.objects.get_or_create(
                slug=slug, defaults={"display": display}
            )
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
        self.stdout.write(self.style.SUCCESS("Enabled summary prerequisites."))
