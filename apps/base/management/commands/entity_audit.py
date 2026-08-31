from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models

from apps.base.models import Entity

EVENT_NAME_TOKENS = ("event", "log", "history", "audit", "snapshot")
VALUE_NAME_TOKENS = ("item", "entry", "link", "mapping", "through", "value", "parameter")


@dataclass
class ModelScorecard:
    label: str
    app_label: str
    model_name: str
    db_table: str
    inherits_entity: bool
    concrete_parent_count: int
    fk_count: int
    one_to_one_count: int
    many_to_many_count: int
    reverse_fk_count: int
    admin_exposed: bool
    event_like_name: bool
    child_like_name: bool
    suggested_bucket: str
    compatibility_risk: str
    rationale: list[str]


class Command(BaseCommand):
    help = "Generate an Entity-adoption inventory and heuristic scorecard for concrete models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("table", "json", "markdown"),
            default="table",
            help="Output format.",
        )

    def handle(self, *args, **options):
        from django.contrib import admin

        admin.autodiscover()

        scorecards = sorted(
            (self._score_model(model) for model in apps.get_models() if not model._meta.proxy),
            key=lambda item: item.label,
        )

        output_format = options["format"]
        if output_format == "json":
            self.stdout.write(json.dumps([asdict(item) for item in scorecards], indent=2))
            return
        if output_format == "markdown":
            self._render_markdown(scorecards)
            return

        self._render_table(scorecards)

    def _score_model(self, model: type[models.Model]) -> ModelScorecard:
        opts = model._meta
        direct_relations = [field for field in opts.get_fields() if field.is_relation and not field.auto_created]
        one_to_one_count = sum(1 for field in direct_relations if isinstance(field, models.OneToOneField))
        fk_count = sum(
            1
            for field in direct_relations
            if isinstance(field, models.ForeignKey) and not isinstance(field, models.OneToOneField)
        )
        many_to_many_count = sum(1 for field in direct_relations if isinstance(field, models.ManyToManyField))
        reverse_fk_count = sum(
            1
            for field in opts.get_fields()
            if field.is_relation
            and field.auto_created
            and not field.concrete
            and getattr(field, "one_to_many", False)
        )

        model_name_tokens = [token.lower() for token in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", opts.object_name)]
        event_like_name = any(token in model_name_tokens for token in EVENT_NAME_TOKENS)
        child_like_name = any(token in model_name_tokens for token in VALUE_NAME_TOKENS)

        has_independent_signals = reverse_fk_count > 0 or many_to_many_count > 0
        is_dependency_shaped = fk_count > 0 and reverse_fk_count == 0 and many_to_many_count == 0

        if event_like_name:
            suggested_bucket = "do_not_adopt"
            rationale = ["Model name suggests append-only event/log record."]
        elif child_like_name and is_dependency_shaped:
            suggested_bucket = "do_not_adopt"
            rationale = ["Model appears to be dependent child/value record."]
        elif has_independent_signals and not is_dependency_shaped:
            suggested_bucket = "adopt_now"
            rationale = ["Model shows independent lifecycle signals."]
        else:
            suggested_bucket = "adopt_later"
            rationale = ["Model needs manual review for lifecycle independence."]

        compatibility_risk = self._compatibility_risk(fk_count, reverse_fk_count, many_to_many_count, one_to_one_count)

        if issubclass(model, Entity):
            rationale.append("Already inherits Entity.")

        return ModelScorecard(
            label=opts.label,
            app_label=opts.app_label,
            model_name=opts.model_name,
            db_table=opts.db_table,
            inherits_entity=issubclass(model, Entity),
            concrete_parent_count=len(opts.parents),
            fk_count=fk_count,
            one_to_one_count=one_to_one_count,
            many_to_many_count=many_to_many_count,
            reverse_fk_count=reverse_fk_count,
            admin_exposed=self._is_admin_exposed(model),
            event_like_name=event_like_name,
            child_like_name=child_like_name,
            suggested_bucket=suggested_bucket,
            compatibility_risk=compatibility_risk,
            rationale=rationale,
        )

    def _compatibility_risk(self, fk_count: int, reverse_fk_count: int, many_to_many_count: int, one_to_one_count: int) -> str:
        relation_weight = (fk_count * 2) + reverse_fk_count + (many_to_many_count * 2) + one_to_one_count
        if relation_weight >= 8:
            return "high"
        if relation_weight >= 4:
            return "medium"
        return "low"

    def _is_admin_exposed(self, model: type[models.Model]) -> bool:
        from django.contrib import admin

        return model in admin.site._registry

    def _render_markdown(self, scorecards: list[ModelScorecard]) -> None:
        self.stdout.write("| Model | Inherits Entity | Suggested Bucket | Compatibility Risk | Rationale |")
        self.stdout.write("|---|---|---|---|---|")
        for item in scorecards:
            inherits_entity = "yes" if item.inherits_entity else "no"
            self.stdout.write(
                f"| `{item.label}` | {inherits_entity} | {item.suggested_bucket} | {item.compatibility_risk} | {'; '.join(item.rationale)} |"
            )

    def _render_table(self, scorecards: list[ModelScorecard]) -> None:
        header = ("label", "entity", "admin", "fk", "o2o", "m2m", "revfk", "bucket", "risk", "rationale")
        rows = [
            (
                item.label,
                "yes" if item.inherits_entity else "no",
                "yes" if item.admin_exposed else "no",
                str(item.fk_count),
                str(item.one_to_one_count),
                str(item.many_to_many_count),
                str(item.reverse_fk_count),
                item.suggested_bucket,
                item.compatibility_risk,
                "; ".join(item.rationale),
            )
            for item in scorecards
        ]

        widths = [max([len(part)] + [len(row[idx]) for row in rows]) for idx, part in enumerate(header)]

        def _line(parts: tuple[str, ...]) -> str:
            return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

        self.stdout.write(_line(header))
        self.stdout.write("-+-".join("-" * width for width in widths))
        for row in rows:
            self.stdout.write(_line(row))
