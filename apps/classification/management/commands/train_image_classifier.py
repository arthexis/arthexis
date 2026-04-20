"""Train an experimental image classifier from verified examples."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.classification.models import ImageClassifierModel
from apps.classification.pipeline import train_classifier


class Command(BaseCommand):
    help = "Train an image classifier from verified examples."

    def add_arguments(self, parser) -> None:
        parser.add_argument("classifier", help="Classifier primary key or slug.")

    def handle(self, *args, **options) -> None:
        classifier = self._resolve_classifier(options["classifier"])
        training_run = train_classifier(classifier)
        self.stdout.write(
            self.style.SUCCESS(
                f"Trained classifier {classifier.slug} with run {training_run.pk} "
                f"using {training_run.sample_count} sample(s)."
            )
        )

    def _resolve_classifier(self, value: str) -> ImageClassifierModel:
        queryset = ImageClassifierModel.objects.all()
        classifier = queryset.filter(pk=int(value)).first() if value.isdigit() else None
        if classifier is None:
            classifier = queryset.filter(slug=value).first()
        if classifier is None:
            raise CommandError(f"No classifier matched '{value}'.")
        return classifier

