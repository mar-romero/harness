import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODEX_CONFIG = ROOT / ".codex" / "config.toml"
DESKTOP_CHECKOUT = Path(r"C:\Users\romer\Documents\GitHub\harnes")
sys.path.insert(0, str(ROOT / "scripts"))

import aci_core
from aci_core import call_tool, repo_read_range, repo_search, tool_definitions


EXPECTED_TOOL_NAMES = [
    "diagnostics_get",
    "git_diff",
    "git_status",
    "lint_run",
    "repo_callers",
    "repo_dependencies",
    "repo_read_range",
    "repo_search",
    "repo_symbol",
    "tests_run",
]


class ACIConfiguredLauncherTests(unittest.TestCase):
    def _server_config(self):
        config = tomllib.loads(CODEX_CONFIG.read_text(encoding="utf-8"))
        return config["mcp_servers"]["harness-aci"]

    def _codex_child_cwd(self, server, host_cwd):
        configured = server.get("cwd")
        self.assertIsNotNone(configured)
        configured_path = Path(configured)
        # Codex passes this value directly as the MCP child process cwd. It is
        # not resolved relative to config.toml or Desktop's outer cwd.
        self.assertTrue(configured_path.is_absolute())
        self.assertEqual(configured_path, DESKTOP_CHECKOUT)
        self.assertNotEqual(host_cwd.resolve(), CODEX_CONFIG.parent.resolve())
        return configured_path

    def _configured_launcher_command(self):
        server = self._server_config()
        return [server["command"], *server.get("args", [])]

    def _candidate_launcher_command(self):
        """Run the branch's launcher while preserving its configured flags."""
        command = self._configured_launcher_command()
        self.assertEqual(command[-2], "-File")
        self.assertEqual(command[-1], "scripts/start_aci_mcp.ps1")
        return [*command[:-1], str(ROOT / command[-1])]

    def _readline(self, stream, proc, timeout=15):
        result = queue.Queue(maxsize=1)
        threading.Thread(target=lambda: result.put(stream.readline()), daemon=True).start()
        try:
            return result.get(timeout=timeout)
        except queue.Empty:
            proc.kill()
            self.fail("timed out waiting for configured MCP launcher response")

    def _exchange(self, host_cwd, env=None, command=None, child_cwd=None):
        server = self._server_config()
        try:
            proc = subprocess.Popen(
                command or self._configured_launcher_command(),
                cwd=child_cwd or self._codex_child_cwd(server, host_cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            self.fail(f"configured MCP launcher could not start: {exc}")
        assert proc.stdin and proc.stdout and proc.stderr
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "codex-launch-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        responses = []
        try:
            for message in messages:
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if "id" in message:
                    line = self._readline(proc.stdout, proc)
                    self.assertTrue(
                        line.strip(),
                        "configured MCP launcher closed before a JSON-RPC response",
                    )
                    responses.append(json.loads(line))
        finally:
            if not proc.stdin.closed:
                proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            stdout_tail = proc.stdout.read()
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
        return responses, proc.returncode, stdout_tail, stderr

    def _assert_legacy_handshake(self, responses, returncode, stdout_tail, stderr):
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        names = [tool["name"] for tool in responses[1]["result"]["tools"]]
        self.assertEqual(names, EXPECTED_TOOL_NAMES)
        self.assertEqual(stdout_tail, "", "MCP stdout contained non-JSON-RPC output")

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher regression")
    def test_candidate_launcher_initializes_from_configured_deployment_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            self.assertNotEqual(external_cwd, ROOT)
            self.assertNotIn(ROOT, external_cwd.parents)
            server = self._server_config()
            # The config's raw cwd is the deployed checkout. The candidate
            # launcher is absolute only because this test runs in a worktree;
            # after merge, Desktop uses the same launcher by its relative arg.
            self.assertEqual(self._codex_child_cwd(server, external_cwd), DESKTOP_CHECKOUT)
            result = self._exchange(external_cwd, command=self._candidate_launcher_command())
        self._assert_legacy_handshake(*result)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher regression")
    def test_configured_launcher_prefers_system_node_over_transient_path_node(self):
        server = self._server_config()
        powershell = shutil.which(server["command"])
        system_node = shutil.which("node.exe")
        self.assertIsNotNone(powershell, "configured PowerShell executable is unavailable")
        self.assertIsNotNone(system_node, "a system Node executable is required for the fixture")
        probe = subprocess.run([system_node, "--version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(probe.returncode, 0, probe.stderr)

        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            hostile_path = external_cwd / "AppData" / "Local" / "fnm_multishells" / "12345_1"
            hostile_path.mkdir(parents=True)
            (hostile_path / "node.exe").write_bytes(b"not a Windows executable")

            empty_program_files = external_cwd / "empty-program-files"
            empty_program_files.mkdir()
            local_appdata = external_cwd / "AppData" / "Local" / "isolated"
            local_appdata.mkdir(parents=True)

            env = os.environ.copy()
            env["PATH"] = os.pathsep.join([str(hostile_path), str(Path(system_node).parent), str(Path(powershell).parent)])
            env["APPDATA"] = str(external_cwd / "empty-appdata")
            env["LOCALAPPDATA"] = str(local_appdata)
            env["ProgramFiles"] = str(empty_program_files)
            env["ProgramW6432"] = str(empty_program_files)
            env["ProgramFiles(x86)"] = str(empty_program_files)
            result = self._exchange(external_cwd, env=env, command=self._candidate_launcher_command(), child_cwd=ROOT)
        self._assert_legacy_handshake(*result)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher regression")
    def test_launcher_falls_back_to_stable_codex_runtime(self):
        server = self._server_config()
        powershell = shutil.which(server["command"])
        system_node = shutil.which("node.exe")
        self.assertIsNotNone(powershell, "configured PowerShell executable is unavailable")
        self.assertIsNotNone(system_node, "a system Node executable is required for the fixture")
        probe = subprocess.run([system_node, "--version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(probe.returncode, 0, probe.stderr)

        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            local_appdata = external_cwd / "AppData" / "Local"
            runtime_node = (
                local_appdata
                / "OpenAI"
                / "Codex"
                / "runtimes"
                / "cua_node"
                / "runtime-1"
                / "bin"
                / "node.exe"
            )
            runtime_node.parent.mkdir(parents=True)
            shutil.copy2(system_node, runtime_node)
            hostile_path = local_appdata / "fnm_multishells" / "12345_1"
            hostile_path.mkdir(parents=True)
            (hostile_path / "node.exe").write_bytes(b"not a Windows executable")
            empty_root = external_cwd / "empty"
            empty_root.mkdir()

            env = os.environ.copy()
            env["PATH"] = os.pathsep.join([str(hostile_path), str(Path(powershell).parent)])
            env["APPDATA"] = str(empty_root)
            env["LOCALAPPDATA"] = str(local_appdata)
            env["ProgramFiles"] = str(empty_root)
            env["ProgramW6432"] = str(empty_root)
            env["ProgramFiles(x86)"] = str(empty_root)
            result = self._exchange(external_cwd, env=env, command=self._candidate_launcher_command(), child_cwd=ROOT)
        self._assert_legacy_handshake(*result)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher regression")
    def test_launcher_fails_cleanly_without_a_stable_node(self):
        server = self._server_config()
        powershell = shutil.which(server["command"])
        self.assertIsNotNone(powershell, "configured PowerShell executable is unavailable")
        with tempfile.TemporaryDirectory() as td:
            external_cwd = Path(td)
            empty_program_files = external_cwd / "empty-program-files"
            empty_program_files.mkdir()
            env = os.environ.copy()
            env["PATH"] = str(Path(powershell).parent)
            env["APPDATA"] = str(external_cwd / "empty-appdata")
            env["LOCALAPPDATA"] = str(external_cwd / "empty-localappdata")
            env["ProgramFiles"] = str(empty_program_files)
            env["ProgramW6432"] = str(empty_program_files)
            env["ProgramFiles(x86)"] = str(empty_program_files)
            command = self._candidate_launcher_command()
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError as exc:
                self.fail(f"configured MCP launcher could not start cleanly: {exc}")
            stdout, stderr = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 127, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr.strip(), "harness-aci: no usable stable Node.js runtime found")


class ACICoreTests(unittest.TestCase):
    def test_raw_configured_cwd_is_the_exact_deployed_checkout(self):
        config = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
        server = config["mcp_servers"]["harness-aci"]
        self.assertEqual(server["cwd"], str(DESKTOP_CHECKOUT))
        launcher = Path(server["args"][-1])
        self.assertFalse(launcher.is_absolute())
        self.assertEqual(launcher, Path("scripts/start_aci_mcp.ps1"))
        # This configuration is intentionally deployed to a Windows checkout.
        # Linux/macOS CI validates its exact serialized contract above, but
        # cannot assert existence of the Windows-only deployment path.
        if sys.platform == "win32":
            self.assertTrue((DESKTOP_CHECKOUT / launcher).is_file())
        self.assertEqual(server["command"].lower(), "powershell.exe")
        self.assertTrue((ROOT / ".codex" / "aci_mcp_entry.py").is_file())

    def test_codex_project_mcp_entrypoint_handles_initialize(self):
        proc = subprocess.Popen(
            [sys.executable, "-u", "aci_mcp_entry.py"],
            cwd=ROOT / ".codex",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin and proc.stdout
        try:
            proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            }) + "\n")
            proc.stdin.flush()
            response = json.loads(proc.stdout.readline())
            self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_tool_catalog_is_small_deterministic_and_typed(self):
        tools = tool_definitions()
        names = [item["name"] for item in tools]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), 10)
        self.assertIn("repo_search", names)
        self.assertIn("tests_run", names)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("outputSchema", tool)

    def test_search_is_bounded_and_finds_known_content(self):
        result = repo_search("Definition of done", path="AGENTS.md", max_results=3)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["matches"])
        self.assertLessEqual(len(result["data"]["matches"]), 3)

    def test_read_range_rejects_outside_repository(self):
        result = repo_read_range("../../etc/passwd", 1, 5)
        self.assertFalse(result["ok"])
        self.assertIn("outside repository", result["error"])

    def test_secret_like_files_are_not_readable_or_searchable(self):
        marker = "aci-secret-" + uuid.uuid4().hex
        secret = ROOT / ".env.aci-test"
        secret.write_text("ACI_SHOULD_NOT_LEAK=" + marker + "\n", encoding="utf-8")
        try:
            read = repo_read_range(str(secret.relative_to(ROOT)), 1, 2)
            self.assertFalse(read["ok"])
            search = repo_search(marker)
            self.assertTrue(search["ok"])
            self.assertEqual(search["data"]["matches"], [])
        finally:
            secret.unlink(missing_ok=True)

    def test_named_profiles_do_not_accept_arbitrary_commands(self):
        result = call_tool("tests_run", {"profile": "echo hacked"})
        self.assertFalse(result["ok"])
        self.assertIn("unknown profile", result["error"])
        result = call_tool("tests_run", {"command": "echo hacked"})
        self.assertFalse(result["ok"])
        self.assertIn("invalid arguments", result["error"])

    def test_python_profiles_use_the_current_interpreter(self):
        self.assertEqual(aci_core._portable_argv(["python", "-m", "unittest"])[0], sys.executable)
        self.assertEqual(aci_core._portable_argv(["python", "-m", "compileall"])[0], sys.executable)
        self.assertEqual(aci_core._portable_argv(["git", "status"]), ["git", "status"])

    def test_runtime_permission_audit_jsonl_is_readable(self):
        with tempfile.TemporaryDirectory() as td, patch.object(aci_core, "ROOT", Path(td)):
            audit = Path(td) / ".harness/opencode/permission-audit.jsonl"
            audit.parent.mkdir(parents=True)
            audit.write_text('{"action":"shell"}\n', encoding="utf-8")
            result = aci_core.repo_read_range(".harness/opencode/permission-audit.jsonl", 1, 2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["lines"][0]["text"], '{"action":"shell"}')


class ACIMCPProtocolTests(unittest.TestCase):
    def _exchange(self, messages, command=None):
        proc = subprocess.Popen(
            command or [sys.executable, str(ROOT / "scripts" / "aci_mcp.py")],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin and proc.stdout
        responses = []
        try:
            for message in messages:
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if "id" in message:
                    line = proc.stdout.readline()
                    self.assertTrue(line, "MCP server closed before responding")
                    responses.append(json.loads(line))
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        return responses

    def test_legacy_initialize_and_tool_call(self):
        responses = self._exchange([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo_read_range", "arguments": {"path": "AGENTS.md", "start_line": 1, "end_line": 2}}},
        ])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(responses[1]["result"]["tools"]), 10)
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(responses[2]["result"]["structuredContent"]["ok"])

    def test_mcp_bridge_reuses_a_worker_for_multiple_requests(self):
        responses = self._exchange([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "repo_read_range", "arguments": {"path": "AGENTS.md", "start_line": 1, "end_line": 1}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo_read_range", "arguments": {"path": "AGENTS.md", "start_line": 2, "end_line": 2}}},
        ])
        self.assertEqual(len(responses[0]["result"]["tools"]), 10)
        self.assertTrue(responses[1]["result"]["structuredContent"]["ok"])
        self.assertTrue(responses[2]["result"]["structuredContent"]["ok"])

    def test_node_bridge_reuses_persistent_worker_for_multiple_requests(self):
        responses = self._exchange([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo_read_range", "arguments": {"path": "AGENTS.md", "start_line": 1, "end_line": 1}}},
        ], command=["node", str(ROOT / "scripts" / "aci_mcp_node.js")])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(responses[1]["result"]["tools"]), 10)
        self.assertTrue(responses[2]["result"]["structuredContent"]["ok"])

    def test_initialize_negotiates_the_client_protocol_version(self):
        requested = "2025-03-26"
        response = self._exchange([{
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": requested, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }])[0]
        self.assertEqual(response["result"]["protocolVersion"], requested)

    def test_modern_discovery_and_tool_list(self):
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        responses = self._exchange([
            {"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {"_meta": meta}},
            {"jsonrpc": "2.0", "id": "l", "method": "tools/list", "params": {"_meta": meta}},
        ])
        self.assertIn("2026-07-28", responses[0]["result"]["supportedVersions"])
        self.assertEqual(responses[0]["result"]["resultType"], "complete")
        self.assertEqual(responses[1]["result"]["resultType"], "complete")
        self.assertEqual(responses[1]["result"]["cacheScope"], "private")
        self.assertEqual(len(responses[1]["result"]["tools"]), 10)


if __name__ == "__main__":
    unittest.main()
