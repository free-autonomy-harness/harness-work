#!/usr/bin/env python3
"""Fetch issue из core и собрать prompt для Hermes (только title + body)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "free-autonomy-harness",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def render(template: str, values: dict[str, object]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


STATE_MARKER = "<!-- harness-state v1 -->"


def load_state(token: str, repo: str, issue: int) -> dict | None:
    """Последнее состояние многошаговой задачи из комментария бота."""
    path = f"/repos/{repo}/issues/{issue}/comments?per_page=100"
    comments = api_get(path, token)
    for comment in reversed(comments):
        body = str(comment.get("body") or "")
        if STATE_MARKER not in body:
            continue
        payload = body.split(STATE_MARKER, 1)[1].strip()
        if payload.startswith("```"):
            parts = payload.split("```")
            if len(parts) >= 3:
                payload = parts[1].strip()
        start, end = payload.find("{"), payload.rfind("}")
        if start >= 0 and end > start:
            payload = payload[start : end + 1]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/harness-core")
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--target", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        print("error: GH_TOKEN is not set", file=sys.stderr)
        return 1

    issue = api_get(f"/repos/{args.repo}/issues/{args.issue}", token)
    state = load_state(token, args.repo, args.issue)
    iteration = int(state.get("iteration", 0)) + 1 if state else 1
    template = Path(args.template).read_text(encoding="utf-8")
    prompt = render(
        template,
        {
            "TARGET": args.target,
            "BASE_SHA": args.base_sha,
            "ISSUE": args.issue,
            "ITERATION": iteration,
            "STATE": (
                json.dumps(state, indent=2, ensure_ascii=False)
                if state
                else "(нет предыдущего состояния — это первый прогон)"
            ),
            "TITLE": issue.get("title", ""),
            "BODY": issue.get("body") or "",
        },
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (out_dir / "issue.json").write_text(
        json.dumps(
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "body": issue.get("body"),
                "labels": [x["name"] for x in issue.get("labels", [])],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"prompt written: {out_dir / 'prompt.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
