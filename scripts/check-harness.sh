#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/check_harness.py
python3 -m unittest discover -s tests -p "test_*.py"
python3 scripts/run_evals.py
