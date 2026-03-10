.PHONY: requirements requirements-check check-imports lint lint-imports lint-strict lint-fix

requirements:
	python scripts/generate_requirements.py

requirements-check:
	python scripts/generate_requirements.py --check


check-imports:
	python scripts/check_import_resolution.py


lint:
	ruff check --select E9,F823 .


lint-imports:
	python scripts/check_import_resolution.py


lint-strict:
	ruff check .


lint-fix:
	ruff check --select E9,F823,I . --fix
	black .
