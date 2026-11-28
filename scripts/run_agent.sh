#!/usr/bin/env bash
# Wrapper: запускает model chain из work/scripts/run_chain.py.
# Аргументы: BASE WORKSPACE OUT PROMPT TARGET ISSUE BASE_SHA
set -euo pipefail

BASE="${1:?base checkout}"
WORKSPACE="${2:?workspace}"
OUT="${3:?out dir}"
PROMPT="${4:?prompt file}"
TARGET="${5:?target alias}"
ISSUE="${6:?issue number}"
BASE_SHA="${7:?base sha}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "${SCRIPT_DIR}/run_chain.py" \
  --chain "${SCRIPT_DIR}/model_chain.json" \
  --prompt "${PROMPT}" \
  --base "${BASE}" \
  --workspace "${WORKSPACE}" \
  --out "${OUT}" \
  --target "${TARGET}" \
  --issue "${ISSUE}" \
  --base-sha "${BASE_SHA}" \
  --image "${HERMES_IMAGE:-nousresearch/hermes-agent@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f}" \
  --disabled-routes "${DISABLED_ROUTES:-}"
