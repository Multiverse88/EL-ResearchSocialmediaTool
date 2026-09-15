#!/usr/bin/env bash
set -euo pipefail

# Ensure we are in the project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Run tests first to ensure no regressions
python3 -m unittest tests/test_core.py > /dev/null 2>&1

# Run the deterministic social media research pipeline benchmark
exec python3 bench.py
