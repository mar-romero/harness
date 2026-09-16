import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aci_core import call_tool


class ACIReadCacheTests(unittest.TestCase):
    def test_same_range_is_not_retransmitted_within_one_runtime_session(self):
        sid = "unit-read-cache-v2"
        cache = ROOT / ".harness" / "cache" / "aci" / f"{sid}.json"
        cache.unlink(missing_ok=True)
        env = {"HARNESS_ACI_SESSION_ID": sid, "HARNESS_TASK_ID": "T-cache", "HARNESS_ROLE": "explorer", "HARNESS_RUNTIME_PROVIDER": "test", "HARNESS_HOOK_AUDIT": "0"}
        with patch.dict(os.environ, env, clear=False):
            first = call_tool("repo_read_range", {"path": "README.md", "start_line": 1, "end_line": 8})
            second = call_tool("repo_read_range", {"path": "README.md", "start_line": 1, "end_line": 8})
            forced = call_tool("repo_read_range", {"path": "README.md", "start_line": 1, "end_line": 8, "force": True})
        self.assertTrue(first["ok"])
        self.assertFalse(first["data"]["cache_hit"])
        self.assertTrue(first["data"]["lines"])
        self.assertTrue(second["data"]["cache_hit"])
        self.assertEqual(second["data"]["lines"], [])
        self.assertFalse(forced["data"]["cache_hit"])
        self.assertTrue(forced["data"]["lines"])
        cache.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
