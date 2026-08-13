#!/usr/bin/env bash
# Runs the integration test suite directly on the host against a local
# Capella install (no Docker) -- fast inner loop for iterating on
# bridge.py. Docker (run_integration_tests.sh) remains the reproducible
# path for CI/other collaborators. See docs/decisions/0003-empacotamento-docker.md.
set -euo pipefail

cd "$(dirname "$0")/.."

export CAPELLA_BIN="${CAPELLA_BIN:-/home/flv/projetos_ita/capella/capella}"
export CAPELLA_MODELS_ROOT="${CAPELLA_MODELS_ROOT:-$(pwd)/tests/fixtures}"

uv sync
uv run pytest tests/test_integration.py -m integration -v
