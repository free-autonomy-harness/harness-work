from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_chain  # noqa: E402


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )


class ChainTest(unittest.TestCase):
    def test_repository_chain_is_valid(self) -> None:
        chain = json.loads((SCRIPTS / "model_chain.json").read_text(encoding="utf-8"))
        run_chain.validate_chain(chain)
        self.assertEqual(len(chain), 4)
        # 3 NVIDIA routes + 1 Groq
        nvidia_routes = [r for r in chain if r["provider"] == "nvidia"]
        groq_routes = [r for r in chain if r["provider"] == "groq"]
        self.assertEqual(len(nvidia_routes), 3)
        self.assertEqual(len(groq_routes), 1)
        # Check order: GLM -> Ultra -> SuperVL -> Groq
        self.assertEqual(chain[0]["name"], "glm-5.3-flash-nvidia")
        self.assertEqual(chain[1]["name"], "nemotron-ultra-nvidia")
        self.assertEqual(chain[2]["name"], "nemotron-super-vl-nvidia")
        self.assertEqual(chain[3]["name"], "gpt-oss-groq")

    def test_nvidia_allowed_models(self) -> None:
        allowed = run_chain.ALLOWED_NVIDIA_MODELS
        self.assertIn(run_chain.NVIDIA_GLM53_FLASH, allowed)
        self.assertIn(run_chain.NVIDIA_ULTRA, allowed)
        self.assertIn(run_chain.NVIDIA_SUPER_VL, allowed)
        self.assertEqual(len(allowed), 3)

    def test_nvidia_disallowed_model_rejected(self) -> None:
        chain = [
            {
                "name": "bad",
                "provider": "nvidia",
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",  # Kilo free model - not allowed
                "key_env": "NVIDIA_API_KEY",
            }
        ]
        with self.assertRaises(run_chain.ChainError):
            run_chain.validate_chain(chain)

    def test_groq_simple_route_valid(self) -> None:
        chain = [
            {
                "name": "simple",
                "provider": "groq",
                "model": run_chain.GROQ_OSS,
                "key_env": "GROQ_API_KEY",
            }
        ]
        run_chain.validate_chain(chain)

    def test_groq_wrong_model_rejected(self) -> None:
        chain = [
            {
                "name": "bad",
                "provider": "groq",
                "model": "meta-llama/llama-3.1-70b",
                "key_env": "GROQ_API_KEY",
            }
        ]
        with self.assertRaises(run_chain.ChainError):
            run_chain.validate_chain(chain)

    def test_route_environment_nvidia(self) -> None:
        route = {
            "name": "nvidia",
            "provider": "nvidia",
            "model": run_chain.NVIDIA_GLM53_FLASH,
            "key_env": "NVIDIA_API_KEY",
        }
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "secret"}, clear=True):
            values = run_chain.route_environment(route)
        self.assertEqual(values["NVIDIA_API_KEY"], "secret")
        self.assertNotIn("CUSTOM_BASE_URL", values)

    def test_per_route_timeout_override(self) -> None:
        route = {"timeout_seconds": 3600}
        self.assertEqual(int(route.get("timeout_seconds") or 600), 3600)
        self.assertEqual(int({}.get("timeout_seconds") or 600), 600)

    def test_make_patch(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        base = Path(tmp.name) / "base"
        workspace = Path(tmp.name) / "workspace"
        base.mkdir()
        git(base, "init", "-q")
        git(base, "config", "user.name", "test")
        git(base, "config", "user.email", "test@local")
        (base / "README.md").write_text("base\n", encoding="utf-8")
        git(base, "add", "-A")
        git(base, "commit", "-qm", "base")

        run_chain.reset_workspace(base, workspace)
        self.assertFalse((workspace / ".git").exists())
        (workspace / "README.md").write_text("changed\n", encoding="utf-8")
        (workspace / "NEW.md").write_text("new\n", encoding="utf-8")
        patch = Path(tmp.name) / "patch.diff"
        path, files = run_chain.make_patch(base, workspace, patch)
        self.assertTrue(path.stat().st_size > 0)
        self.assertIn("README.md", files)
        self.assertIn("NEW.md", files)
        self.assertIn("README.md", patch.read_text(encoding="utf-8"))
        tmp.cleanup()

    def test_docker_command_uses_hermes_uid(self) -> None:
        route = {
            "name": "nvidia",
            "provider": "nvidia",
            "model": run_chain.NVIDIA_GLM53_FLASH,
            "key_env": "NVIDIA_API_KEY",
        }
        cmd = run_chain.docker_command(
            route,
            prompt="hi",
            workspace=Path("/tmp/ws"),
            out_dir=Path("/tmp/out"),
            env_file=Path("/tmp/env"),
            image="image@sha256:abc",
            container_name="harness-test",
        )
        joined = " ".join(cmd)
        self.assertNotIn("--user", joined)
        self.assertIn("HERMES_UID", joined)
        self.assertIn("--safe-mode", joined)
        self.assertIn("--toolsets file,terminal", joined)
        self.assertIn("image@sha256:abc", joined)
        # egress allowlist: entrypoint + NET_ADMIN
        self.assertIn("--entrypoint", joined)
        self.assertIn("--cap-add=NET_ADMIN", joined)


if __name__ == "__main__":
    unittest.main()