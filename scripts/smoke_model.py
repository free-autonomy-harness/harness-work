#!/usr/bin/env python3
"""Smoke одного маршрута model_chain.json через официальный Hermes image."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_chain import DEFAULT_IMAGE, log, run_route, validate_chain  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True)
    parser.add_argument("--chain", default=str(Path(__file__).with_name("model_chain.json")))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--image", default=os.environ.get("HERMES_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--json-out", default=None, help="write machine-readable result here")
    args = parser.parse_args(argv)

    chain = json.loads(Path(args.chain).read_text(encoding="utf-8"))
    validate_chain(chain)
    route = next((r for r in chain if r["name"] == args.route), None)
    if route is None:
        print(f"error: unknown route {args.route}", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="harness-smoke-"))
    base = tmp / "base"
    workspace = tmp / "workspace"
    out = tmp / "out"
    base.mkdir()
    out.mkdir()
    try:
        subprocess.run(["git", "init", "-q", str(base)], check=True)
        (base / "README.md").write_text("smoke\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(base), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(base), "-c", "user.name=harness", "-c", "user.email=harness@local", "commit", "-qm", "init"],
            check=True,
        )
        result = run_route(
            route,
            prompt="Reply with exactly: OK",
            base=base,
            workspace=workspace,
            out_dir=out,
            timeout=args.timeout,
            image=args.image,
        )
        log_file = out / f"route-{route['name']}.log"
        text = log_file.read_text(encoding="utf-8").strip() if log_file.exists() else ""
        if result.get("exit") == 0 and not text:
            result["exit"] = 1
            result["error"] = "empty model output"
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if text:
            print(text[-4000:])
        return 0 if result.get("exit") == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
