import argparse

def parse_args(args=None):
    parser = argparse.ArgumentParser(
        prog="buxn",
        description="uxn-bench: A testing and benchmarking framework for Uxn."
    )
    
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run tests and benchmarks (default)")
    run_parser.add_argument("--watch", "-w", action="store_true", help="Run in watch mode")
    run_parser.add_argument("--include", "-i", type=str, action="append", help="Include tests matching glob (can be specified multiple times)")
    run_parser.add_argument("--exclude", "-e", type=str, action="append", help="Exclude tests matching glob (can be specified multiple times)")
    run_parser.add_argument("--quiet", "-q", action="store_true", help="Fail-only mode (hide successful tests)")
    run_parser.add_argument("--plain", "-p", action="store_true", help="Plain line-by-line output (no live tree)")
    run_parser.add_argument("--log-file", "-l", type=str, default="buxn.log", help="Path to write detailed verification logs")
    run_parser.add_argument("--timeout", "-t", type=float, default=2.0, help="Timeout in seconds for each test execution")
    run_parser.add_argument("--fail-fast", "-x", action="store_true", help="Stop on first failure")
    run_parser.add_argument("--config", "-c", type=str, default="uxn-bench.json", help="Path to configuration file")
    
    # Vendor command
    vendor_parser = subparsers.add_parser("vendor", help="Fetch and build vendor tools (uxnasm, uxncli, uxn2)")
    
    return parser.parse_args(args)
