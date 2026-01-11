from __future__ import annotations

import uuid

from django.core.management import BaseCommand, CommandError
from django.db.models import Q

from apps.recipes.models import Recipe
from apps.recipes.utils import parse_recipe_arguments, serialize_recipe_result


class Command(BaseCommand):
    help = "Run a stored recipe by slug or UUID."

    def add_arguments(self, parser):
        parser.add_argument("recipe", help="Recipe slug or UUID")
        parser.add_argument(
            "recipe_args",
            nargs="*",
            help="Arguments passed to the recipe. Use key=value for named args.",
        )

    def handle(self, *args, **options):
        identifier = options["recipe"]
        uuid_query = Q()
        try:
            uuid_query = Q(uuid=uuid.UUID(identifier))
        except (TypeError, ValueError):
            pass
        recipe = Recipe.objects.filter(Q(slug=identifier) | uuid_query).first()

        if recipe is None:
            raise CommandError(f"Recipe not found: {identifier}")

        positional, keyword = parse_recipe_arguments(options["recipe_args"])
        execution = recipe.execute(*positional, **keyword)
        output = serialize_recipe_result(execution.result)
        self.stdout.write(output)
