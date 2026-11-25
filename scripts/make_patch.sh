#!/usr/bin/env bash
# Собрать патч из рабочего дерева агента.
# Дерево копируется без .git в чистый worktree на base SHA.
# Аргументы: BASE WORKSPACE OUT_PATCH
set -euo pipefail

BASE="${1:?base checkout}"
WORKSPACE="${2:?workspace}"
OUT="${3:?output patch}"

TMP="$(mktemp -d)"
trap 'git -C "${BASE}" worktree remove --force "${TMP}/patchrepo" 2>/dev/null || true; rm -rf "${TMP}"' EXIT

git -C "${BASE}" worktree add --detach "${TMP}/patchrepo" HEAD >/dev/null
rsync -a --delete --exclude .git "${WORKSPACE}/" "${TMP}/patchrepo/"
git -C "${TMP}/patchrepo" add -A
git -C "${TMP}/patchrepo" diff --cached --binary > "${OUT}"
test -s "${OUT}"
git -C "${BASE}" apply --check --binary "${OUT}"
