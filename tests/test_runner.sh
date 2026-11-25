#!/usr/bin/env bash
# Smoke-тесты wrapper: chain policy, patch generation, docker command.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."
python3 -m unittest discover -s tests -v
