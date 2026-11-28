#!/usr/bin/env python3
"""Model chain runner: Hermes в Docker, fallback по маршрутам, сборка патча.

Один маршрут = один контейнер. Workspace сбрасывается перед каждой попыткой.
Ключ маршрута передаётся только в его контейнер через env-file (mode 0600).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_IMAGE = "nousresearch/hermes-agent@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f"
NVIDIA_GLM53_FLASH = "z-ai/glm-5.3-flash"
NVIDIA_ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_SUPER_VL = "nvidia/nemotron-3.5-super-vl-120b-a12b"

ALLOWED_NVIDIA_MODELS = {
    NVIDIA_GLM53_FLASH,
    NVIDIA_ULTRA,
    NVIDIA_SUPER_VL,
}


class ChainError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def validate_chain(chain: list[dict[str, Any]]) -> None:
    if not chain:
        raise ChainError("model chain is empty")
    names: set[str] = set()
    for route in chain:
        name = str(route.get("name") or "")
        provider = str(route.get("provider") or "")
        model = str(route.get("model") or "")
        key_env = str(route.get("key_env") or "")
        if not name or not provider or not model or not key_env:
            raise ChainError(f"incomplete route: {route}")
        if name in names:
            raise ChainError(f"duplicate route name: {name}")
        names.add(name)
        if provider == "nvidia":
            if model not in ALLOWED_NVIDIA_MODELS:
                raise ChainError(f"{name}: NVIDIA model not allowed: {model}. Allowed: {sorted(ALLOWED_NVIDIA_MODELS)}")
            if key_env != "NVIDIA_API_KEY":
                raise ChainError(f"{name}: nvidia route must use NVIDIA_API_KEY")
        else:
            raise ChainError(f"{name}: unsupported provider {provider}")


def route_environment(route: dict[str, Any]) -> dict[str, str]:
    key_env = str(route["key_env"])
    key = os.environ.get(key_env, "").strip()
    if not key:
        raise ChainError(f"{key_env} is not set")
    return {key_env: key}


def reset_workspace(base: Path, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    proc = subprocess.run(
        ["rsync", "-a", "--exclude", ".git", f"{base}/", f"{workspace}/"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ChainError(f"cannot reset workspace: {proc.stderr[-2000:]}")


def make_patch(base: Path, workspace: Path, out_patch: Path) -> tuple[Path, list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="harness-patch-"))
    patchrepo = tmp / "patchrepo"
    try:
        proc = subprocess.run(
            ["git", "-C", str(base), "worktree", "add", "--detach", str(patchrepo), "HEAD"],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise ChainError(f"git worktree add failed: {proc.stderr[-2000:]}")
        sync = subprocess.run(
            ["rsync", "-a", "--delete", "--exclude", ".git", f"{workspace}/", f"{patchrepo}/"],
            text=True,
            capture_output=True,
        )
        if sync.returncode != 0:
            raise ChainError(f"rsync workspace failed: {sync.stderr[-2000:]}")
        add = subprocess.run(["git", "-C", str(patchrepo), "add", "-A"], text=True, capture_output=True)
        if add.returncode != 0:
            raise ChainError(f"git add failed: {add.stderr[-2000:]}")
        diff = subprocess.run(
            ["git", "-C", str(patchrepo), "diff", "--cached", "--binary"],
            text=True,
            capture_output=True,
        )
        if diff.returncode != 0:
            raise ChainError(f"git diff failed: {diff.stderr[-2000:]}")
        if not diff.stdout.strip():
            raise ChainError("empty patch")
        out_patch.parent.mkdir(parents=True, exist_ok=True)
        out_patch.write_text(diff.stdout, encoding="utf-8")
        names = subprocess.run(
            ["git", "-C", str(patchrepo), "diff", "--cached", "--name-only"],
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        check = subprocess.run(
            ["git", "-C", str(base), "apply", "--check", "--binary", str(out_patch)],
            text=True,
            capture_output=True,
        )
        if check.returncode != 0:
            raise ChainError(f"generated patch does not apply: {check.stderr[-2000:]}")
        return out_patch, [n for n in names if n]
    finally:
        subprocess.run(
            ["git", "-C", str(base), "worktree", "remove", "--force", str(patchrepo)],
            text=True,
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def verify_patch(
    base: Path, workspace: Path, checks: list[dict[str, Any]], target: str = ""
) -> list[dict[str, Any]]:
    """Прогнать verify-команды target по патчу до того, как считать маршрут успешным.

    Проверки идут в отдельном worktree на base SHA: workspace служит только
    источником файлов, а поведение совпадает с проверкой в publish.
    """
    if not checks:
        return []
    tmp = Path(tempfile.mkdtemp(prefix="harness-verify-"))
    worktree = tmp / "verifyrepo"
    results: list[dict[str, Any]] = []
    env = os.environ.copy()
    env.setdefault("CI", "1")
    if target:
        env["HARNESS_TARGET"] = target
    try:
        added = subprocess.run(
            ["git", "-C", str(base), "worktree", "add", "--detach", str(worktree), "HEAD"],
            text=True,
            capture_output=True,
        )
        if added.returncode != 0:
            raise ChainError(f"verify worktree add failed: {added.stderr[-2000:]}")
        synced = subprocess.run(
            ["rsync", "-a", "--delete", "--exclude", ".git", f"{workspace}/", f"{worktree}/"],
            text=True,
            capture_output=True,
        )
        if synced.returncode != 0:
            raise ChainError(f"verify worktree sync failed: {synced.stderr[-2000:]}")
        for check in checks:
            name = str(check.get("name") or "check")
            command = str(check.get("run") or "")
            timeout = int(check.get("timeout_seconds") or 600)
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    ["bash", "-lc", command],
                    cwd=str(worktree),
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                ok = proc.returncode == 0
                detail = (proc.stderr or proc.stdout or "")[-2000:]
            except subprocess.TimeoutExpired:
                ok = False
                detail = f"timeout after {timeout}s"
            results.append(
                {
                    "name": name,
                    "ok": ok,
                    "duration_s": round(time.monotonic() - started, 2),
                    "error": None if ok else detail,
                }
            )
        return results
    finally:
        subprocess.run(
            ["git", "-C", str(base), "worktree", "remove", "--force", str(worktree)],
            text=True,
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def docker_command(
    route: dict[str, Any],
    *,
    prompt: str,
    workspace: Path,
    out_dir: Path,
    env_file: Path,
    image: str,
    container_name: str,
) -> list[str]:
    uid = os.getuid()
    gid = os.getgid()
    script_dir = Path(__file__).parent.resolve()
    egress_script = script_dir / "egress-allowlist.sh"
    provider = str(route["provider"])
    model = str(route["model"])
    # Hermes command (what we would run directly)
    hermes_cmd = [
        "-z",
        prompt,
        "--provider",
        provider,
        "-m",
        model,
        "--toolsets",
        "file,terminal",
        "--safe-mode",
        "--ignore-user-config",
        "--in",
        "/workspace",
        "--usage-file",
        f"/out/usage-{route['name']}.json",
    ]
    # Wrap with egress allowlist entrypoint
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        # Capabilities needed for iptables inside container
        "--cap-add=NET_ADMIN",
        "--cap-add=NET_RAW",
        # Образ использует s6-overlay и должен стартовать как root/PID 1.
        # HERMES_UID/HERMES_GID переmap-ят пользователя hermes на host UID,
        # чтобы файлы в /workspace принадлежали runner-пользователю.
        "-e",
        f"HERMES_UID={uid}",
        "-e",
        f"HERMES_GID={gid}",
        "-e",
        "PYTHONUNBUFFERED=1",
        # Образ задаёт HERMES_WRITE_SAFE_ROOT=/opt/data, а target смонтирован
        # в /workspace. Без переопределения write_file не может создать файлы
        # в рабочем дереве, из которого собирается патч.
        "-e",
        f"HERMES_WRITE_SAFE_ROOT=/workspace{os.pathsep}/opt/data",
        "--env-file",
        str(env_file),
        "-v",
        f"{workspace}:/workspace",
        "-v",
        f"{out_dir}:/out",
        "-v",
        f"{egress_script}:/egress-allowlist.sh:ro",
        "-w",
        "/workspace",
        "--entrypoint",
        "/egress-allowlist.sh",
        image,
        provider,
        model,
        "--",
        *hermes_cmd,
    ]
    return cmd


def run_route(
    route: dict[str, Any],
    *,
    prompt: str,
    base: Path,
    workspace: Path,
    out_dir: Path,
    timeout: int,
    image: str,
) -> dict[str, Any]:
    # Per-route timeout (timeout_seconds в chain) важнее глобального:
    # GLM-5.3-Flash на Cloudflare холодно стартует и требует больше времени.
    timeout = int(route.get("timeout_seconds") or timeout)
    reset_workspace(base, workspace)
    env_values = route_environment(route)
    env_file = out_dir / f"env-{route['name']}.tmp"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env_values.items()), encoding="utf-8")
    env_file.chmod(0o600)
    container_name = f"harness-{uuid.uuid4().hex[:12]}"
    log_path = out_dir / f"route-{route['name']}.log"
    started = time.monotonic()
    try:
        cmd = docker_command(
            route,
            prompt=prompt,
            workspace=workspace,
            out_dir=out_dir,
            env_file=env_file,
            image=image,
            container_name=container_name,
        )
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        duration = round(time.monotonic() - started, 2)
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        log_path.write_text(redact(output, env_values.values()), encoding="utf-8")
        if proc.returncode != 0:
            return {
                "route": route["name"],
                "exit": proc.returncode,
                "duration_s": duration,
                "error": f"exit {proc.returncode}: {redact(proc.stderr[-2000:], env_values.values())}",
            }
        return {"route": route["name"], "exit": 0, "duration_s": duration, "error": None}
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", container_name], text=True, capture_output=True)
        return {
            "route": route["name"],
            "exit": None,
            "duration_s": round(time.monotonic() - started, 2),
            "error": f"timeout after {timeout}s",
        }
    except OSError as exc:
        return {
            "route": route["name"],
            "exit": None,
            "duration_s": round(time.monotonic() - started, 2),
            "error": f"cannot start docker: {exc}",
        }
    finally:
        env_file.unlink(missing_ok=True)


def redact(text: str, secrets: Any) -> str:
    result = text
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            result = result.replace(str(secret), "***")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", default=str(Path(__file__).with_name("model_chain.json")))
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--base", required=True, help="target checkout на base SHA (с .git)")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("HERMES_TIMEOUT_SECONDS", "2700")))
    parser.add_argument("--image", default=os.environ.get("HERMES_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--disabled-routes", default="", help="comma-separated route names to skip")
    parser.add_argument("--verify-json", default="", help="JSON-список verify-проверок target")
    args = parser.parse_args(argv)

    verify_checks: list[dict[str, Any]] = []
    if args.verify_json:
        try:
            parsed = json.loads(args.verify_json)
        except json.JSONDecodeError as exc:
            raise ChainError(f"invalid --verify-json: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(c, dict) for c in parsed):
            raise ChainError("--verify-json must be a JSON list of objects")
        verify_checks = [c for c in parsed if c.get("run")]

    chain = json.loads(Path(args.chain).read_text(encoding="utf-8"))
    validate_chain(chain)
    prompt = Path(args.prompt).read_text(encoding="utf-8")
    base = Path(args.base).resolve()
    workspace = Path(args.workspace).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[dict[str, Any]] = []
    patch_path = out_dir / "patch.diff"
    meta: dict[str, Any] = {
        "target": args.target,
        "issue": args.issue,
        "base_sha": args.base_sha,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": attempts,
    }

    disabled = {name.strip() for name in args.disabled_routes.split(",") if name.strip() not in ("", "none")}
    for route in chain:
        if route["name"] in disabled:
            log(f"skip disabled route {route['name']} (from smoke gate)")
            continue
        log(f"try {route['name']} ({route['provider']}/{route['model']})")
        try:
            attempt = run_route(
                route,
                prompt=prompt,
                base=base,
                workspace=workspace,
                out_dir=out_dir,
                timeout=args.timeout,
                image=args.image,
            )
        except ChainError as exc:
            attempt = {"route": route["name"], "exit": None, "error": str(exc)}
        attempts.append(attempt)
        if attempt.get("exit") != 0:
            log(f"route {route['name']} failed: {attempt.get('error')}")
            continue
        try:
            patch_path, files = make_patch(base, workspace, patch_path)
        except ChainError as exc:
            attempt["error"] = str(exc)
            log(f"route {route['name']} produced no patch: {exc}")
            continue

        if verify_checks:
            try:
                check_results = verify_patch(base, workspace, verify_checks, args.target)
            except ChainError as exc:
                attempt["error"] = str(exc)
                log(f"route {route['name']} could not be verified: {exc}")
                continue
            attempt["verify"] = check_results
            failed = [c for c in check_results if not c["ok"]]
            if failed:
                detail = "; ".join(
                    f"{c['name']}: {c.get('error') or 'exit!=0'}" for c in failed
                )
                attempt["error"] = f"verify failed: {detail}"
                log(f"route {route['name']} rejected by verify: {detail}")
                continue

        usage = out_dir / f"usage-{route['name']}.json"
        if usage.exists():
            shutil.copyfile(usage, out_dir / "usage.json")
        meta.update(
            {
                "status": "success",
                "route": route["name"],
                "provider": route["provider"],
                "model": route["model"],
                "patch_sha256": sha256(patch_path),
                "files": files,
                "error": None,
            }
        )
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"success via {route['name']}, files={len(files)}")
        return 0

    meta["status"] = "failed"
    meta["error"] = "all routes failed"
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log("all routes failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
