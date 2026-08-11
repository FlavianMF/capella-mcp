#!/usr/bin/env bash
# Runs the integration test suite inside a container of the capella-mcp
# image, so tests/test_integration.py sees a real CAPELLA_BIN. See
# docs/decisions/0002-headless-por-chamada.md.
set -euo pipefail

cd "$(dirname "$0")/.."

docker build -t capella-mcp .

docker run --rm \
    -v "$(pwd)/tests:/app/tests" \
    -v "$(pwd)/tests/fixtures:/workspace/models" \
    --entrypoint bash \
    capella-mcp \
    -c "uv sync --frozen && uv run pytest tests/test_integration.py -m integration -v"
