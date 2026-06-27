import sys
from .cli import parse_args
from .logger import setup_logger, get_logger
from .config import load_config
from . import vendor
from .discovery import DiscoveryEngine

def main():
    args = parse_args()
    setup_logger(args.verbose)
    logger = get_logger("main")
    
    if args.command == "vendor":
        vendor.run(args)
        sys.exit(0)
    
    if args.command == "undump":
        from .coredump import undump_command
        success, output = undump_command(args.file, verbose=args.verbose)
        if success:
            print(output)
            sys.exit(0)
        else:
            print(output, file=sys.stderr)
            sys.exit(1)
        
    # Default behavior for 'run' or no command specified
    config_path = getattr(args, "config", "buxn.json")
    
    logger.debug("Starting uxn-bench harness (Verbose mode enabled)")
    
    config = load_config(config_path)
    if not config:
        logger.error("No valid configuration found. Exiting.")
        sys.exit(1)
        
    logger.info(f"Loaded configuration successfully. Keys: {list(config.keys())}")
    
    # Run test discovery
    engine = DiscoveryEngine()
    includes = getattr(args, "include", None)
    excludes = getattr(args, "exclude", None)
    
    tests = engine.discover(includes=includes, excludes=excludes)
    
    if not tests:
        logger.info("No tests discovered. Exiting.")
        sys.exit(0)
        
    # Execution & Verification
    from .reporter import ExecutionReporter
    
    logger.info("Executing tests...")
    
    reporter = ExecutionReporter(
        tests=tests,
        config=config,
        quiet=getattr(args, "quiet", False),
        verbose=getattr(args, "verbose", False),
        plain=getattr(args, "plain", False),
        log_file=getattr(args, "log_file", "buxn.log"),
        fail_fast=getattr(args, "fail_fast", False),
        timeout=getattr(args, "timeout", 2.0)
    )
    
    reporter.run_all()
    
if __name__ == "__main__":
    main()
