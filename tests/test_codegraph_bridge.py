import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from codegraph_bridge import _run


class CodeGraphBridgeTests(unittest.TestCase):
    def test_run_rejects_provider_output_beyond_bound_without_retaining_it(self):
        class FakeProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"x" * 1000)
                self.stderr = io.BytesIO(b"y" * 1000)

            def poll(self):
                return 0

            def wait(self):
                return 0

            def kill(self):
                return None

        with patch("codegraph_bridge._command_prefix", return_value=["codegraph"]), \
                patch("codegraph_bridge.subprocess.Popen", return_value=FakeProcess()):
            result = _run(["explore"], max_chars=10)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "process output limit exceeded")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"]), 10)
        self.assertLessEqual(len(result["stderr"]), 10)


if __name__ == "__main__":
    unittest.main()
