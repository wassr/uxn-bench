import unittest

from buxn.verifier import parse_coredump, Verifier
from buxn.discovery import TestNode

class TestVerifier(unittest.TestCase):
    def test_parse_coredump_valid(self):
        magic = b"UXNC"
        version = bytes([1])
        wst_ptr = bytes([3])
        rst_ptr = bytes([0])
        wst_data = bytes([i % 256 for i in range(256)])
        rst_data = bytes([0] * 256)
        ram_data = bytes([0] * 65536)
        
        data = magic + version + wst_ptr + rst_ptr + wst_data + rst_data + ram_data
        
        parsed = parse_coredump(data)
        self.assertEqual(parsed["wst_ptr"], 3)
        self.assertEqual(parsed["rst_ptr"], 0)
        self.assertEqual(parsed["wst"], wst_data)
        self.assertEqual(parsed["rst"], rst_data)
        self.assertEqual(parsed["ram"], ram_data)
        
    def test_parse_coredump_invalid_size(self):
        with self.assertRaises(ValueError):
            parse_coredump(b"too_short")
            
    def test_parse_coredump_invalid_magic(self):
        data = b"BADM" + bytes([1, 0, 0]) + bytes(256 + 256 + 65536)
        with self.assertRaisesRegex(ValueError, "Bad coredump magic"):
            parse_coredump(data)

    def test_parse_coredump_invalid_version(self):
        data = b"UXNC" + bytes([99, 0, 0]) + bytes(256 + 256 + 65536)
        with self.assertRaisesRegex(ValueError, "Unsupported coredump version"):
            parse_coredump(data)

    def test_check_stack_field_list(self):
        v = Verifier()
        # Stack wraps around: 255, 0, 1
        data = bytearray(256)
        data[255] = 10
        data[0] = 20
        data[1] = 30
        
        # Test logical read ending at ptr = 2
        # So elements at 255, 0, 1 -> [10, 20, 30]
        results = v._check_stack_field("wst", [10, 20, 30], bytes(data), 2)
        self.assertTrue(results[0].passed)
        
        results = v._check_stack_field("wst", [10, 20, 31], bytes(data), 2)
        self.assertFalse(results[0].passed)

    def test_check_stack_field_dict(self):
        v = Verifier()
        data = bytearray(256)
        data[255] = 42
        data[10] = 99
        
        results = v._check_stack_field("wst", {"255": [42], "10": [99]}, bytes(data), 0)
        self.assertTrue(all(r.passed for r in results))

    def test_check_top_field(self):
        v = Verifier()
        data = bytearray(256)
        data[255] = 88
        data[0] = 99
        
        # ptr=0 means top is at 255
        res = v._check_top_field("wst_top", 88, bytes(data), 0)
        self.assertTrue(res.passed)
        
        # ptr=1 means top is at 0
        res = v._check_top_field("wst_top", 99, bytes(data), 1)
        self.assertTrue(res.passed)

if __name__ == '__main__':
    unittest.main()
