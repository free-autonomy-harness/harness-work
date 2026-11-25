#!/usr/bin/env python3
"""work/notify: после work/agent dispatch core/publish.

Читает meta.json из artifact, иначе парсит run-name `agent <target> #<issue>`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def api(method: str, path: str, token: str, data: dict | None = None) -> dict | None:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "free-autonomy-harness",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
        return json.loads(payload) if payload else None


def parse_run_name(name: str) -> tuple[str | None, int | None]:
    match = re.search(r"agent\s+([a-z0-9_-]+)\s+#(\d+)", name or "", re.IGNORECASE)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        print("error: GITHUB_EVENT_PATH is missing", file=sys.stderr)
        return 1
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    run = event.get("workflow_run") or {}
    run_id = run.get("id")
    if not run_id:
        print("error: workflow_run.id is missing", file=sys.stderr)
        return 1

    token = os.environ.get("GH_TOKEN", "").strip()
    core_repo = os.environ.get("CORE_REPO", "").strip()
    work_repo = os.environ.get("WORK_REPO", "").strip() or run.get("repository", {}).get("full_name", "")
    if not token or not core_repo:
        print("error: GH_TOKEN and CORE_REPO are required", file=sys.stderr)
        return 1

    artifact_dir = Path(os.environ.get("ARTIFACT_DIR", "artifact"))
    target: str | None = None
    issue: int | None = None
    meta_path = artifact_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        target = str(meta.get("target") or "") or None
        issue = int(meta["issue"]) if meta.get("issue") is not None else None
    if not target or not issue:
        target, issue = parse_run_name(str(run.get("name") or ""))

    if not target or not issue:
        print(f"error: cannot determine target/issue from run {run_id}", file=sys.stderr)
        return 1

    ref = (api("GET", f"/repos/{core_repo}", token) or {}).get("default_branch", "main")
    api(
        "POST",
        f"/repos/{core_repo}/actions/workflows/publish.yml/dispatches",
        token,
        {
            "ref": ref,
            "inputs": {
                "target": target,
                "issue": str(issue),
                "run_id": str(run_id),
                "work_repo": work_repo,
            },
        },
    )
    print(f"dispatched publish: {target} #{issue} run={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
