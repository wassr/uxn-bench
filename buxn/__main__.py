import sys
import subprocess
import shlex
from pathlib import Path
from .cli import parse_args
from .logger import setup_logger, get_logger
from .config import load_config
from . import vendor
from .discovery import DiscoveryEngine
from .runner import Runner, BUXN_TMP_DIR

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
    
    if args.command == "asm":
        _run_asm_command(args)
        sys.exit(0)
    
    if args.command == "run":
        _run_run_command(args)
        sys.exit(0)
    
    # Default behavior for 'test' or no command specified
    config_path = getattr(args, "config", "buxn.json")
    
    logger.debug("Starting uxn-bench harness (Verbose mode enabled)")
    
    config = load_config(config_path)
    if not config:
        logger.error("No valid configuration found. Exiting.")
        sys.exit(1)
        
    if getattr(args, "assembler", None):
        if args.assembler not in config.get("assemblers", {}):
            logger.error(f"Assembler '{args.assembler}' not found in configuration.")
            sys.exit(1)
        config.setdefault("defaults", {})["assembler"] = args.assembler

    if getattr(args, "interpreter", None):
        if args.interpreter not in config.get("interpreters", {}):
            logger.error(f"Interpreter '{args.interpreter}' not found in configuration.")
            sys.exit(1)
        config.setdefault("defaults", {})["interpreter"] = args.interpreter
        
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


def _run_asm_command(args):
    """Run the asm command to assemble a file."""
    logger = get_logger("asm")
    
    # Load config to get assembler definitions
    config_path = "buxn.json"
    config = load_config(config_path)
    if not config:
        logger.error("No valid configuration found. Exiting.")
        sys.exit(1)
    
    # Get the assembler spec
    asm_spec = config.get("assemblers", {}).get(args.assembler, {})
    if not asm_spec:
        logger.error(f"Assembler '{args.assembler}' not found in configuration.")
        sys.exit(1)
    
    asm_cmd_template = asm_spec.get("cmd", f"{args.assembler} {{input}} {{output}}")
    
    # Build the command
    input_file = Path(args.file)
    if not input_file.exists():
        logger.error(f"Input file not found: {args.file}")
        sys.exit(1)
    
    # Use the same pattern as the runner
    runner = Runner(config)
    output_file = Path(f"{input_file.stem}.rom")
    
    cmd = runner._build_argv(args.assembler, asm_cmd_template, {
        "input": input_file,
        "output": output_file,
    })
    
    logger.info(f"Assembling {args.file} with {args.assembler}: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
        sys.exit(result.returncode)
    except Exception as e:
        logger.error(f"Error running assembler: {e}")
        sys.exit(1)


def _run_run_command(args):
    """Run the run command to execute a file with an interpreter."""
    logger = get_logger("run")
    
    # Load config to get interpreter definitions
    config_path = "buxn.json"
    config = load_config(config_path)
    if not config:
        logger.error("No valid configuration found. Exiting.")
        sys.exit(1)
    
    # Get the interpreter spec
    int_spec = config.get("interpreters", {}).get(args.interpreter, {})
    if not int_spec:
        logger.error(f"Interpreter '{args.interpreter}' not found in configuration.")
        sys.exit(1)
    
    int_cmd_template = int_spec.get("cmd", f"{args.interpreter} {{rom}}")
    supports_coredump = "cpu/coredump" in int_spec.get("capabilities", [])
    
    # Build the command
    input_file = Path(args.file)
    if not input_file.exists():
        logger.error(f"Input file not found: {args.file}")
        sys.exit(1)
    
    runner = Runner(config)
    
    # If --asm flag is set and input is not a .rom file, assemble first
    if args.asm and not input_file.suffix.lower() == ".rom":
        # Assemble the file
        asm_tool_name = config.get("defaults", {}).get("assembler", "uxnasm")
        asm_spec = config.get("assemblers", {}).get(asm_tool_name, {})
        asm_cmd_template = asm_spec.get("cmd", f"{asm_tool_name} {{input}} {{output}}")
        
        temp_rom = Path(f"{input_file.stem}.rom")
        asm_cmd = runner._build_argv(asm_tool_name, asm_cmd_template, {
            "input": input_file,
            "output": temp_rom,
        })
        
        logger.info(f"Assembling {args.file} first: {' '.join(asm_cmd)}")
        try:
            asm_result = subprocess.run(asm_cmd, capture_output=True, text=True)
            if asm_result.returncode != 0:
                logger.error(f"Assembly failed: {asm_result.stderr}")
                print(asm_result.stderr, file=sys.stderr)
                sys.exit(asm_result.returncode)
            input_file = temp_rom
        except Exception as e:
            logger.error(f"Error running assembler: {e}")
            sys.exit(1)
    
    # Now run with the interpreter
    dump_file = Path(f"{input_file.stem}.core.dump")
    cmd = runner._build_argv(args.interpreter, int_cmd_template, {
        "rom": input_file,
        "dump_file": dump_file if supports_coredump else "",
    })
    
    logger.info(f"Running {input_file} with {args.interpreter}: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
        sys.exit(result.returncode)
    except Exception as e:
        logger.error(f"Error running interpreter: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
