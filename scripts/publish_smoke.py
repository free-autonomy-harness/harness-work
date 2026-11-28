#!/usr/bin/env python3
"""Собрать результаты smoke и записать HERMES_DISABLED_ROUTES в repo variable.

Используется после matrix-задачи check-models. Рабочие маршруты остаются в
доступном списке, упавшие — пропускаются work/agent в этом прогоне.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

VAR_NAME = "HERMES_DISABLED_ROUTES"
API = "https://api.github.com"


def api(method: str, path: str, token: str, data: dict[str, Any] | None = None) -> Any:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "free-autonomy-harness",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API {exc.code} for {path}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc


def set_variable(repo: str, token: str, value: str) -> None:
    path = f"/repos/{repo}/actions/variables/{urllib.parse.quote(VAR_NAME, safe='')}"
    try:
        api("PATCH", path, token, {"name": VAR_NAME, "value": value})
    except RuntimeError as exc:
        if "404" not in str(exc):
            raise
        api("POST", f"/repos/{repo}/actions/variables", token, {"name": VAR_NAME, "value": value})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        print("error: GH_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 1

    results_dir = Path(args.results_dir)
    files = sorted(results_dir.glob("result-*.json"))
    if not files:
        print("error: no smoke results found", file=sys.stderr)
        return 1

    disabled: list[str] = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        route = str(data.get("route") or path.stem.removeprefix("result-"))
        if data.get("exit") != 0:
            disabled.append(route)
            print(f"[gate] disabled: {route}: {str(data.get('error'))[:200]}")
        else:
            print(f"[gate] ok: {route}")

    value = ",".join(disabled) if disabled else "none"
    set_variable(repo, token, value)
    print(f"[gate] {VAR_NAME}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
