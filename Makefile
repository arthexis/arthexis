.PHONY: requirements requirements-check check-imports

requirements:
	python scripts/generate_requirements.py

requirements-check:
	python scripts/generate_requirements.py --check


check-imports:
	python scripts/check_import_resolution.py
