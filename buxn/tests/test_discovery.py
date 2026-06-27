import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from buxn.discovery import DiscoveryEngine, TestNode

class TestDiscoveryEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.tests_path = Path(self.temp_dir.name)
        
        # Create some dummy tests
        self._create_test("assembler/basic", {"name": "Basic Assm"})
        self._create_test("cpu/add", {"name": "Add Instr"})
        self._create_test("cpu/sub", {"name": "Sub Instr"})
        
    def tearDown(self):
        self.temp_dir.cleanup()
        
    def _create_test(self, test_id: str, config: dict):
        p = self.tests_path / f"{test_id}.test.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(config, f)
            
    def test_discovery_all(self):
        engine = DiscoveryEngine(tests_dir=str(self.tests_path))
        tests = engine.discover()
        self.assertEqual(len(tests), 3)
        ids = [t.id for t in tests]
        self.assertIn("assembler/basic", ids)
        self.assertIn("cpu/add", ids)
        self.assertIn("cpu/sub", ids)
        
    def test_discovery_include(self):
        engine = DiscoveryEngine(tests_dir=str(self.tests_path))
        tests = engine.discover(includes=["cpu/*"])
        self.assertEqual(len(tests), 2)
        ids = [t.id for t in tests]
        self.assertIn("cpu/add", ids)
        self.assertIn("cpu/sub", ids)
        
    def test_discovery_exclude(self):
        engine = DiscoveryEngine(tests_dir=str(self.tests_path))
        tests = engine.discover(excludes=["cpu/*"])
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0].id, "assembler/basic")
        
    def test_discovery_include_exclude(self):
        engine = DiscoveryEngine(tests_dir=str(self.tests_path))
        tests = engine.discover(includes=["cpu/*"], excludes=["*/sub"])
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0].id, "cpu/add")

if __name__ == '__main__':
    unittest.main()
