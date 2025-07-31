from django.core.management.base import BaseCommand

from todos.utils import create_todos_from_comments


class Command(BaseCommand):
    help = "Create Todo items from '# TODO' comments in the codebase."

    def handle(self, *args, **options):
        create_todos_from_comments()
        self.stdout.write(self.style.SUCCESS("Todo items imported."))
