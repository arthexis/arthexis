from io import StringIO
import json

from django.apps import apps
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Dump data marked as seed data into a fixture file"

    def add_arguments(self, parser):
        parser.add_argument(
            "outfile",
            nargs="?",
            default="seed_data.json",
            help="Output fixture file",
        )

    def handle(self, *args, **options):
        outfile = options["outfile"]
        models = [
            apps.get_model("sites", "Site"),
            apps.get_model("ocpp", "Charger"),
            apps.get_model("accounts", "RFID"),
            apps.get_model("references", "Reference"),
        ]
        objects = []
        for model in models:
            pks = list(
                model.objects.filter(is_seed_data=True).values_list("pk", flat=True)
            )
            if not pks:
                continue
            out = StringIO()
            call_command(
                "dumpdata",
                f"{model._meta.app_label}.{model._meta.model_name}",
                "--pks",
                ",".join(str(pk) for pk in pks),
                stdout=out,
            )
            objects.extend(json.loads(out.getvalue()))
        with open(outfile, "w") as fh:
            json.dump(objects, fh, indent=2)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(objects)} objects to {outfile}"))
