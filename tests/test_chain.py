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
        self.assertEqual(chain[0]["model"], run_chain.KILO_ULTRA)
        self.assertEqual(chain[0]["provider"], "kilocode")

    def test_glm_on_kilo_rejected(self) -> None:
        chain = [
            {
                "name": "bad",
                "provider": "kilocode",
                "model": "zai-org/glm-5.2:free",
                "key_env": "KILOCODE_API_KEY",
            }
        ]
        with self.assertRaises(run_chain.ChainError):
            run_chain.validate_chain(chain)

    def test_groq_simple_route_valid(self) -> None:
        chain = [
            {
                "name": "simple",
                "provider": "groq",
                "model": "openai/gpt-oss-120b",
                "key_env": "GROQ_API_KEY",
            }
        ]
        run_chain.validate_chain(chain)

    def test_route_environment_kilo(self) -> None:
        route = {
            "name": "kilo",
            "provider": "kilocode",
            "model": run_chain.KILO_ULTRA,
            "key_env": "KILOCODE_API_KEY",
        }
        env = {"KILOCODE_API_KEY": "secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            values = run_chain.route_environment(route)
        self.assertEqual(values["KILOCODE_API_KEY"], "secret")
        self.assertNotIn("CUSTOM_BASE_URL", values)

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
            "name": "kilo",
            "provider": "kilocode",
            "model": run_chain.KILO_ULTRA,
            "key_env": "KILOCODE_API_KEY",
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


if __name__ == "__main__":
    unittest.main()
