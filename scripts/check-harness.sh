#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/check_harness.py
python -m unittest discover -s tests -p "test_*.py"
python scripts/run_evals.py
