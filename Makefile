.PHONY: requirements requirements-check

requirements:
	python scripts/generate_requirements.py

requirements-check:
	python scripts/generate_requirements.py --check
