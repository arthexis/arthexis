#!/usr/bin/env bash
set -euo pipefail

pytest -m "critical or regression"
