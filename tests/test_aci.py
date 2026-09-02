import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aci_core import call_tool, repo_read_range, repo_search, tool_definitions


class ACICoreTests(unittest.TestCase):
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


class ACIMCPProtocolTests(unittest.TestCase):
    def _exchange(self, messages):
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "aci_mcp.py")],
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
