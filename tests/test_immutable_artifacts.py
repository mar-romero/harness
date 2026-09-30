import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harnesslib  # noqa: E402


class WriteJsonExclusiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.target = self.dir / "impact.json"

    def test_destination_is_absent_until_content_is_complete(self):
        observed = []
        real_dump = json.dump

        def spying_dump(data, handle, **kwargs):
            observed.append(self.target.exists())
            real_dump(data, handle, **kwargs)

        with mock.patch.object(harnesslib.json, "dump", side_effect=spying_dump):
            harnesslib.write_json_exclusive(self.target, {"plan": 1})
        self.assertEqual(observed, [False])
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"plan": 1})

    def test_existing_destination_is_never_replaced(self):
        self.target.write_text('{"plan": "first"}\n', encoding="utf-8")
        with self.assertRaises(FileExistsError):
            harnesslib.write_json_exclusive(self.target, {"plan": "second"})
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"plan": "first"}\n')

    def test_no_temporary_files_are_left_behind(self):
        harnesslib.write_json_exclusive(self.target, {"plan": 1})
        with self.assertRaises(FileExistsError):
            harnesslib.write_json_exclusive(self.target, {"plan": 2})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["impact.json"])

    def test_filesystem_without_hard_links_still_creates_exclusively(self):
        with mock.patch.object(harnesslib.os, "link", side_effect=OSError("links unsupported")):
            harnesslib.write_json_exclusive(self.target, {"plan": 1})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"plan": 1})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["impact.json"])


class AdoptJsonImmutableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "context.json"

    def test_first_writer_creates_the_artifact(self):
        self.assertEqual(harnesslib.adopt_json_immutable(self.target, {"v": 1}), {"v": 1})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"v": 1})

    def test_later_writer_adopts_the_frozen_value_without_overwriting(self):
        harnesslib.adopt_json_immutable(self.target, {"built_by": "first"})
        before = self.target.read_bytes()
        adopted = harnesslib.adopt_json_immutable(self.target, {"built_by": "second"})
        self.assertEqual(adopted, {"built_by": "first"})
        self.assertEqual(self.target.read_bytes(), before)

    def test_corrupt_artifact_fails_closed(self):
        self.target.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            harnesslib.adopt_json_immutable(self.target, {"v": 1})


if __name__ == "__main__":
    unittest.main()
