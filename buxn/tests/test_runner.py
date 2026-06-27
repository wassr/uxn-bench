import unittest
from pathlib import Path

from buxn.runner import Runner

class TestRunner(unittest.TestCase):
    def setUp(self):
        self.config = {
            "defaults": {
                "assembler": "uxnasm",
                "interpreter": "uxn2"
            },
            "assemblers": {
                "uxnasm": {
                    "cmd": "uxnasm {input} {output}"
                }
            },
            "interpreters": {
                "uxn2": {
                    "cmd": "uxn2 {rom}"
                },
                "python-minimal": {
                    "cmd": "python3 vm.py {rom} --dump-on-halt {dump_file}",
                    "capabilities": ["cpu/coredump"]
                }
            }
        }
        self.runner = Runner(self.config)
        self.runner.bin_dir = Path("/tmp/fake_bin") # Prevent it from picking up real vendor binaries
        
    def test_build_argv_simple(self):
        cmd = self.runner._build_argv("uxnasm", "uxnasm {input} {output}", {
            "input": "in.tal",
            "output": "out.rom"
        })
        self.assertEqual(cmd, ["uxnasm", "in.tal", "out.rom"])
        
    def test_build_argv_with_dump(self):
        cmd = self.runner._build_argv("python-minimal", "python3 vm.py {rom} --dump-on-halt {dump_file}", {
            "rom": "out.rom",
            "dump_file": "core.dump"
        })
        self.assertEqual(cmd, ["python3", "vm.py", "out.rom", "--dump-on-halt", "core.dump"])
        
    def test_build_argv_empty_token(self):
        cmd = self.runner._build_argv("python-minimal", "python3 vm.py {rom} {dump_file}", {
            "rom": "out.rom",
            "dump_file": ""
        })
        # shlex.split handles empty space
        self.assertEqual(cmd, ["python3", "vm.py", "out.rom"])

if __name__ == '__main__':
    unittest.main()
