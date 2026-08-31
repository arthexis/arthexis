from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.certs.models import CertificateBase


class Command(BaseCommand):
    help = "Verify certificates against filesystem state and validity."

    def add_arguments(self, parser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--ids",
            nargs="+",
            type=int,
            help="CertificateBase IDs to verify.",
        )
        group.add_argument(
            "--all",
            action="store_true",
            help="Verify all certificates.",
        )
        parser.add_argument(
            "--sudo",
            default="sudo",
            help="Prefix commands with sudo (use an empty string to disable).",
        )

    def handle(self, *args, **options):
        ids = options.get("ids")
        sudo = options.get("sudo") or ""

        queryset = CertificateBase.objects.all()
        if ids:
            queryset = queryset.filter(id__in=ids)

        if not queryset.exists():
            raise CommandError("No certificates matched the provided selection.")

        failures = 0
        for certificate in queryset:
            result = certificate.verify(sudo=sudo)
            status = "OK" if result.ok else "FAIL"
            line = f"[{status}] {certificate} - {result.summary}"
            if result.ok:
                self.stdout.write(self.style.SUCCESS(line))
            else:
                self.stdout.write(self.style.ERROR(line))
                failures += 1

        if failures:
            raise CommandError(f"{failures} certificate(s) failed verification.")
