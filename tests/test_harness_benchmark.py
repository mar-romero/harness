import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "hb",
    ROOT / "scripts/harness_benchmark.py",
)
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


class T(unittest.TestCase):
    def test_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)

            case_path = tmp / "case.json"
            case_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "BENCH-TEST",
                        "task": {
                            "description": "Minimal benchmark validation case"
                        },
                        "workspace": {
                            "source": "."
                        },
                        "verification": {
                            "public_checks": []
                        },
                    }
                ),
                encoding="utf-8",
            )

            suite_path = tmp / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "test-suite",
                        "cases": ["case.json"],
                    }
                ),
                encoding="utf-8",
            )

            suite, cases = hb.load_suite(suite_path)

            self.assertEqual(suite["name"], "test-suite")
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0][1]["id"], "BENCH-TEST")

    def test_usage(self):
        usage = hb.extract_executor_usage(
            '{"usage":{"input_tokens":100,"output_tokens":20,'
            '"total_tokens":120},"cost_usd":0.03}'
        )

        self.assertEqual(usage["tokens"], 120)
        self.assertEqual(usage["cost_usd"], 0.03)

    def test_aggregate(self):
        rows = [
            {
                "variant": "baseline",
                "metrics": {
                    "task_success": 0.0,
                    "first_pass_success": 0.0,
                    "hidden_test_pass_rate": 0.0,
                    "bug_introduction": 1.0,
                    "mutation_score": None,
                    "impact_recall": None,
                    "tdd_valid_red": None,
                    "human_intervention": 0.0,
                    "tokens": 100,
                    "cost_usd": 1,
                    "latency_seconds": 10,
                    "agents_used": 1,
                },
            },
            {
                "variant": "full",
                "metrics": {
                    "task_success": 1.0,
                    "first_pass_success": 1.0,
                    "hidden_test_pass_rate": 1.0,
                    "bug_introduction": 0.0,
                    "mutation_score": None,
                    "impact_recall": 1.0,
                    "tdd_valid_red": 1.0,
                    "human_intervention": 0.0,
                    "tokens": 150,
                    "cost_usd": 1.5,
                    "latency_seconds": 15,
                    "agents_used": 4,
                },
            },
        ]

        aggregate, delta = hb.aggregate(rows, ["baseline", "full"])

        self.assertEqual(
            aggregate["full"]["metrics"]["task_success_rate"]["mean"],
            1.0,
        )
        self.assertEqual(
            delta["full"]["task_success_rate"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()