import json
import tempfile
import unittest
from pathlib import Path

from buxn.discovery import DiscoveryEngine, TestNode
from buxn.runner import ExecutionResult
from buxn.validation import validate_fixture
from buxn.verifier import Verifier


class TestFixtureValidation(unittest.TestCase):
    def test_invalid_verification_is_rejected(self):
        for spec in (None, [], {}, {"type": []}, {"type": "stdout_match"},
                     {"type": "stdout_match", "expected_stdout": ""},
                     {"type": "stdout_match", "expected_stdout": "OK", "match": []},
                     {"type": "stdout_match", "expected_stdout": "OK", "typo": 1},
                     {"type": "coredump", "ram": {"0": []}},
                     {"type": "coredump", "wst": []}):
            with self.subTest(spec=spec):
                test = TestNode("example", "", "Example", Path("test.json"), {"verify": spec})
                self.assertFalse(Verifier().verify(test, ExecutionResult(0, "OK", "", {}, 0)).passed)

    def test_invalid_fixtures_cannot_disappear(self):
        for content in ("{broken", "[]", json.dumps({"run": {"input": "x.tal"}, "verify": []})):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "broken.test.json"
                path.write_text(content)
                with self.assertRaisesRegex(ValueError, "broken.test.json"):
                    DiscoveryEngine(tmp).discover()
                # Explicitly excluded files do not invalidate the selected suite.
                self.assertEqual(DiscoveryEngine(tmp).discover(excludes=["broken"]), [])

    def test_exact_and_substring_output_are_distinct(self):
        for kind, key in (("stdout_match", "expected_stdout"), ("stderr_match", "expected_stderr")):
            for mode, expected, actual, passes in (("exact", "", "", True), ("exact", "", "unexpected", False),
                                                  ("exact", "OK", "OK extra", False), ("contains", "OK", "OK extra", True)):
                with self.subTest(kind=kind, mode=mode, actual=actual):
                    node = TestNode("example", "", "Example", Path("test.json"), {"verify": {"type": kind, key: expected, "match": mode}})
                    result = ExecutionResult(0, actual if kind == "stdout_match" else "", actual if kind == "stderr_match" else "", {}, 0)
                    self.assertEqual(Verifier().verify(node, result).passed, passes)

    def test_error_match_requires_actual_program_failure(self):
        node = TestNode("example", "", "Example", Path("test.json"), {"verify": {"type": "error_match", "expected_stderr": "bad"}})
        for mode in ("contains", "exact"):
            node.config["verify"]["match"] = mode
            for code, passes in ((0, False), (-1, False), (1, True)):
                self.assertEqual(Verifier().verify(node, ExecutionResult(code, "", "bad", {}, 0)).passed, passes)

    def test_repository_fixtures_are_valid(self):
        root = Path(__file__).resolve().parents[2]
        for directory, pattern in (("tests", "*.test.json"), ("benchmarks", "*.bench.json")):
            for path in (root / directory).rglob(pattern):
                with self.subTest(path=path):
                    validate_fixture(json.loads(path.read_text()))
