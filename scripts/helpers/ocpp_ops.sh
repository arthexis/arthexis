#!/usr/bin/env bash
set -euo pipefail

# Operational helper snippets for the unified OCPP management command.

python manage.py ocpp coverage --version 1.6 --json-path media/ocpp16_coverage.json --badge-path media/ocpp16_coverage.svg
python manage.py ocpp coverage --version 2.0.1 --json-path media/ocpp201_coverage.json --badge-path media/ocpp201_coverage.svg
python manage.py ocpp coverage --version 2.1 --json-path media/ocpp21_coverage.json --badge-path media/ocpp21_coverage.svg

# Example transaction/trace workflows:
# python manage.py ocpp transactions export /tmp/ocpp_transactions.json --start 2025-01-01
# python manage.py ocpp transactions import /tmp/ocpp_transactions.json
# python manage.py ocpp trace extract --txn 123 --out /tmp/ocpp_extract.json --log /tmp/ocpp_trace.log
# python manage.py ocpp trace replay /tmp/ocpp_extract.json
