import argparse

def parse_args(args=None):
    parser = argparse.ArgumentParser(
        prog="buxn",
        description="uxn-bench: A testing and benchmarking framework for Uxn."
    )
    
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")
    
    # Test command
    test_parser = subparsers.add_parser("test", help="Run tests and benchmarks (default)")
    test_parser.add_argument("--watch", "-w", action="store_true", help="Run in watch mode")
    test_parser.add_argument("--include", "-i", type=str, action="append", help="Include tests matching glob (can be specified multiple times)")
    test_parser.add_argument("--exclude", "-e", type=str, action="append", help="Exclude tests matching glob (can be specified multiple times)")
    test_parser.add_argument("--quiet", "-q", action="store_true", help="Fail-only mode (hide successful tests)")
    test_parser.add_argument("--plain", "-p", action="store_true", help="Plain line-by-line output (no live tree)")
    test_parser.add_argument("--log-file", "-l", type=str, default="buxn.log", help="Path to write detailed verification logs")
    test_parser.add_argument("--timeout", "-t", type=float, default=2.0, help="Timeout in seconds for each test execution")
    test_parser.add_argument("--fail-fast", "-x", action="store_true", help="Stop on first failure")
    test_parser.add_argument("--config", "-c", type=str, default="buxn.json", help="Path to configuration file")
    test_parser.add_argument("--assembler", "-a", type=str, help="Override default assembler")
    test_parser.add_argument("--interpreter", "-r", type=str, help="Override default interpreter")
    
    # Vendor command
    vendor_parser = subparsers.add_parser("vendor", help="Fetch and build vendor tools (uxnasm, uxncli, uxn2)")
    
    # Asm command
    asm_parser = subparsers.add_parser("asm", help="Assemble a file using a specific assembler")
    asm_parser.add_argument("assembler", type=str, help="Assembler to use (e.g., uxnasm)")
    asm_parser.add_argument("file", type=str, help="Path to the assembly file to assemble")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run a ROM or assembly file using a specific interpreter")
    run_parser.add_argument("interpreter", type=str, help="Interpreter to use (e.g., uxn2, uxnemu, pys)")
    run_parser.add_argument("file", type=str, help="Path to the file to run (ROM or assembly)")
    run_parser.add_argument("-a", "--asm", action="store_true", help="Assemble the file first using the default assembler")
    
    # Undump command
    undump_parser = subparsers.add_parser("undump", help="Display a coredump file in human-readable format")
    undump_parser.add_argument("file", type=str, help="Path to the coredump file")
    undump_parser.add_argument("--verbose", "-v", action="store_true", help="Show full hex dumps")
    
    return parser.parse_args(args)
