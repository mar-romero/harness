import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hook_bus import post_agent


class HookBusV2Tests(unittest.TestCase):
    def test_writer_protected_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
            (repo / "AI_POLICY.md").write_text("changed", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"HARNESS_HOOK_AUDIT": "0"}, clear=False):
                result = post_agent(task_id="T-hook", role="implementer", mode="writer", cwd=repo, provider="codex", exit_code=0)
            self.assertFalse(result["allow"])
            self.assertEqual(result["exit_code"], 74)
            self.assertTrue(any(x["path"] == "AI_POLICY.md" for x in result["violations"]))


if __name__ == "__main__":
    unittest.main()
