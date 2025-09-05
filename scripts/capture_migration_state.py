#!/usr/bin/env python3
"""Capture migration plan, inspectdb output, and optional schema dump for a release."""
import subprocess
import sys
import pathlib
import os


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: capture_migration_state.py <version>")
        sys.exit(1)
    version = sys.argv[1]
    out_dir = pathlib.Path("releases") / version
    out_dir.mkdir(parents=True, exist_ok=True)

    # Capture migrations plan
    plan = subprocess.check_output(
        ["python", "manage.py", "showmigrations", "--plan"], text=True
    )
    (out_dir / "migration-plan.txt").write_text(plan)

    # Capture inspectdb output
    inspect = subprocess.check_output(
        ["python", "manage.py", "inspectdb"], text=True
    )
    (out_dir / "inspectdb.py").write_text(inspect)

    # Optional: dump full schema using pg_dump if available
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        import django
        from django.conf import settings

        django.setup()
        db_name = settings.DATABASES["default"]["NAME"]
        schema_path = out_dir / "schema.sql"
        with schema_path.open("w") as fh:
            subprocess.run(["pg_dump", "--schema-only", db_name], check=True, stdout=fh)
    except Exception as exc:  # pragma: no cover - optional
        # Schema dump is optional; print message and continue
        print(f"Skipping schema dump: {exc}")

    # Add files to git
    files = [out_dir / "migration-plan.txt", out_dir / "inspectdb.py"]
    schema_file = out_dir / "schema.sql"
    if schema_file.exists():
        files.append(schema_file)
    subprocess.run(["git", "add", *map(str, files)], check=True)


if __name__ == "__main__":  # pragma: no cover - script entry
    main()
