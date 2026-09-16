import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from model_router import selections_for_task
from subscription_bridge import build_inventory
from subscription_runtime import build_command, execute, sanitized_environment
import subscription_runtime


class SubscriptionBridgeTests(unittest.TestCase):
    def test_execute_delivers_large_prompt_via_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()

            subprocess.run(
                ["git", "init"],
                cwd=repo,
                capture_output=True,
                check=True,
            )

            bindir = Path(td) / "bin"
            bindir.mkdir()
            self._fake_cursor(bindir, mutate=False)

            env = dict(os.environ)
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
            env["HARNESS_HOOK_AUDIT"] = "0"

            huge_prompt = "HARNESS-LARGE-PROMPT-" + ("x" * 100_000)

            with patch.dict(os.environ, env, clear=True):
                result = execute(
                    provider="cursor",
                    prompt=huge_prompt,
                    model="auto",
                    effort=None,
                    role="reviewer",
                    mode="read-only",
                    cwd=repo,
                    max_turns=3,
                    timeout=10,
                )

            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["final_text"], "saw-large-prompt")

    def test_execute_uses_utf8_for_subprocess_text_transport(self):

        completed = subprocess.CompletedProcess(
            args=["fake"],
            returncode=0,
            stdout='{"response":"ok"}',
            stderr="",
        )

        with patch.object(subscription_runtime, "find_executable", return_value="fake-cli"), \
            patch.object(
                subscription_runtime,
                "probe_capabilities",
                return_value={"probe_ok": True, "features": {}},
            ), \
            patch.object(subscription_runtime, "hook_pre_agent", return_value={"allow": True}), \
            patch.object(
                subscription_runtime,
                "hook_post_agent",
                side_effect=lambda **kwargs: {"exit_code": kwargs["exit_code"]},
            ), \
            patch.object(subscription_runtime, "git_fingerprint", return_value="same"), \
            patch.object(subscription_runtime.subprocess, "run", return_value=completed) as run_mock:

            result = subscription_runtime.execute(
                provider="cursor",
                prompt="Leé: á é í ó ú ñ ¿ ¡",
                model="auto",
                effort=None,
                role="reviewer",
                mode="read-only",
                cwd=Path("."),
                max_turns=1,
                timeout=10,
            )

        self.assertEqual(result["exit_code"], 0)

        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "strict")
        self.assertIn("á é í ó ú ñ ¿ ¡", kwargs["input"])

    def test_strict_environment_removes_direct_model_api_keys(self):
        base = {
            "PATH": os.environ.get("PATH", ""),
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "secret2",
            "CURSOR_API_KEY": "secret3",
        }
        env, removed = sanitized_environment("codex", base)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertIn("OPENAI_API_KEY", removed)
        self.assertIn("ANTHROPIC_API_KEY", env)  # provider-scoped: do not erase unrelated auth

    def test_all_command_builders_are_shell_free_and_role_scoped(self):
        cwd = Path("/tmp/repo")
        cases = {
            "codex": ("codex", "gpt-5.3-codex"),
            "claude": ("claude", "sonnet"),
            "copilot": ("copilot", "gpt-5.4"),
            "cursor": ("cursor-agent", "auto"),
            "grok": ("grok", "grok-4.6"),
            "gemini": ("gemini", "auto"),
        }
        for provider, (exe, model) in cases.items():
            with self.subTest(provider=provider):
                argv = build_command(
                    provider=provider, executable=exe, prompt="inspect", model=model,
                    effort="high", mode="read-only", cwd=cwd, max_turns=12,
                    output_file=Path("/tmp/final") if provider == "codex" else None,
                )
                self.assertIsInstance(argv, list)
                self.assertEqual(argv[0], exe)
                self.assertNotIn("shell=True", argv)
                joined = " ".join(argv)
                if provider == "codex": self.assertIn("read-only", joined)
                if provider == "claude":
                    self.assertIn("plan", joined)
                    self.assertIn("--effort", argv)
                    self.assertTrue(any("mcp__harness-aci__repo_explore" == x for x in argv))
                if provider == "copilot":
                    self.assertIn("--deny-tool=write", joined)
                    self.assertIn("--allow-tool=harness-aci", argv)
                if provider == "cursor":
                    self.assertNotIn("--force", argv)
                    self.assertIn("--mode=ask", argv)
                if provider == "grok": self.assertIn("dontAsk", joined)
                if provider == "gemini": self.assertIn("plan", joined)

    def test_large_prompts_are_not_embedded_in_argv_for_stdin_capable_providers(self):
        cwd = Path("/tmp/repo")
        huge_prompt = "HARNESS-LARGE-PROMPT-" + ("x" * 100_000)

        cases = {
            "codex": ("codex", "gpt-5.6-sol"),
            "claude": ("claude", "sonnet"),
            "copilot": ("copilot", "gpt-5.4"),
            "cursor": ("cursor-agent", "auto"),
        }

        for provider, (exe, model) in cases.items():
            with self.subTest(provider=provider):
                argv = build_command(
                    provider=provider,
                    executable=exe,
                    prompt=huge_prompt,
                    model=model,
                    effort="medium",
                    mode="read-only",
                    cwd=cwd,
                    max_turns=12,
                    output_file=Path("/tmp/final") if provider == "codex" else None,
                )

                # The payload must never be part of the Windows command line.
                self.assertNotIn(huge_prompt, argv)
                self.assertFalse(any(huge_prompt in str(arg) for arg in argv))

                # argv itself should remain tiny regardless of prompt size.
                command_length = sum(len(str(arg)) + 1 for arg in argv)
                self.assertLess(command_length, 8192)

                if provider == "codex":
                    self.assertEqual(argv[-1], "-")

                if provider == "claude":
                    self.assertIn("-p", argv)

                if provider == "copilot":
                    self.assertNotIn("-p", argv)
                    self.assertNotIn("--prompt", argv)

                if provider == "cursor":
                    self.assertIn("-p", argv)
                    self.assertIn("--trust", argv)

    def test_inventory_can_feed_existing_cross_provider_model_router(self):
        inventory = build_inventory(["codex", "claude", "copilot", "cursor", "grok", "gemini"], include_missing=True)
        self.assertEqual(inventory["provider"], "subscriptions")
        self.assertTrue(any(m["id"].startswith("claude/") for m in inventory["models"]))
        self.assertTrue(any(m["id"].startswith("copilot/") for m in inventory["models"]))
        task = {"id": "T-subs", "description": "Implement a normal code refactor with tests", "files": ["README.md"]}
        selections = selections_for_task(task, "subscriptions", inventory)
        self.assertTrue(selections)
        used = [x for x in selections if x.get("action") == "use"]
        self.assertTrue(used)
        self.assertTrue(all("/" in x["base_model_id"] for x in used))

    def _fake_cursor(self, directory: Path, mutate: bool) -> Path:
        mutation = "Path('mutated.txt').write_text('x')" if mutate else "pass"

        if os.name == "nt":
            # Windows cannot execute a shebang-only extensionless script through
            # shutil.which/subprocess like Unix can. Create a Python payload plus
            # a normal .cmd launcher so `cursor-agent` is discoverable via PATHEXT.
            payload = directory / "cursor-agent.py"
            payload.write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "if '--help' in sys.argv or '-h' in sys.argv:\n"
                "    print('usage: agent [options] [prompt...] --mode --output-format --sandbox')\n"
                "    raise SystemExit(0)\n"
                "if '--version' in sys.argv or '-v' in sys.argv:\n"
                "    print('cursor-agent test-double 1.0')\n"
                "    raise SystemExit(0)\n"
                f"{mutation}\n"
                "data = sys.stdin.read()\n"
                "response = 'saw-large-prompt' if 'HARNESS-LARGE-PROMPT-' in data else 'ok'\n"
                "print(json.dumps({'response':response,'usage':{'input_tokens':11,'output_tokens':3},'saw_api_key':bool(os.getenv('CURSOR_API_KEY'))}))\n",
                encoding="utf-8",
            )

            launcher = directory / "cursor-agent.cmd"
            launcher.write_text(
                f'@echo off\r\n"{sys.executable}" "{payload}" %*\r\n',
                encoding="utf-8",
            )
            return launcher

        script = directory / "cursor-agent"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if '--help' in sys.argv or '-h' in sys.argv:\n"
            "    print('usage: agent [options] [prompt...] --mode --output-format --sandbox')\n"
            "    raise SystemExit(0)\n"
            "if '--version' in sys.argv or '-v' in sys.argv:\n"
            "    print('cursor-agent test-double 1.0')\n"
            "    raise SystemExit(0)\n"
            f"{mutation}\n"
            "data = sys.stdin.read()\n"
            "response = 'saw-large-prompt' if 'HARNESS-LARGE-PROMPT-' in data else 'ok'\n"
            "print(json.dumps({'response':response,'usage':{'input_tokens':11,'output_tokens':3},'saw_api_key':bool(os.getenv('CURSOR_API_KEY'))}))\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_read_only_runtime_detects_mutation_and_uses_subscription_sanitization(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"; repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
            bindir = Path(td) / "bin"; bindir.mkdir()
            self._fake_cursor(bindir, mutate=False)
            env = dict(os.environ)
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
            env["CURSOR_API_KEY"] = "must-not-reach-child"
            env["HARNESS_HOOK_AUDIT"] = "0"
            with patch.dict(os.environ, env, clear=True):
                result = execute(provider="cursor", prompt="inspect", model="auto", effort=None,
                                 role="reviewer", mode="read-only", cwd=repo, max_turns=3, timeout=10)
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(result["integrity"]["changed"])
            self.assertIn("CURSOR_API_KEY", result["removed_env"])

            self._fake_cursor(bindir, mutate=True)
            with patch.dict(os.environ, env, clear=True):
                changed = execute(provider="cursor", prompt="inspect", model="auto", effort=None,
                                  role="reviewer", mode="read-only", cwd=repo, max_turns=3, timeout=10)
            self.assertEqual(changed["exit_code"], 73)
            self.assertTrue(changed["integrity"]["changed"])


if __name__ == "__main__":
    unittest.main()
