import os
import sys
import json
import time
import platform
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from .logger import get_logger
from .discovery import DiscoveryEngine, TestNode
from .runner import Runner

logger = get_logger(__name__)

HISTORY_DIR = Path(".buxn/benchmarks/history")

def get_system_metadata() -> dict:
    """Collect platform, CPU, and memory environment information."""
    metadata = {
        "os": f"{platform.system()} {platform.release()}",
        "cpu_arch": platform.machine(),
        "cpu_cores": os.cpu_count(),
        "cpu_model": "unknown",
        "memory_bytes": 0
    }
    
    # Resolve CPU model and memory on macOS
    if platform.system() == "Darwin":
        try:
            cpu_model = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
            metadata["cpu_model"] = cpu_model
        except Exception:
            pass
        try:
            memsize = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            metadata["memory_bytes"] = int(memsize)
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        metadata["cpu_model"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        metadata["memory_bytes"] = int(line.split()[1]) * 1024
                        break
        except Exception:
            pass
            
    return metadata

def save_benchmark_run(run_data: dict):
    """Archiving results grouped by interpreter name."""
    interpreter = run_data["results"][0]["interpreter"] if run_data["results"] else "unknown"
    version = run_data["results"][0]["interpreter_version"] if run_data["results"] else "unknown"
    
    # Safe timestamp filename (replace colons)
    ts = run_data["timestamp"].replace(":", "-")
    
    dest_dir = HISTORY_DIR / interpreter
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    dest_file = dest_dir / f"{version}_{ts}.json"
    with open(dest_file, "w") as f:
        json.dump(run_data, f, indent=2)
        
    logger.info(f"Saved benchmark results to {dest_file}")

def get_latest_run_file(interpreter: str = None) -> Optional[Path]:
    """Retrieve the most recent benchmark run file."""
    if not HISTORY_DIR.exists():
        return None
        
    all_files = []
    search_path = HISTORY_DIR / interpreter if interpreter else HISTORY_DIR
    for path in search_path.rglob("*.json"):
        all_files.append(path)
        
    if not all_files:
        return None
        
    # Sort files by creation time
    all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_files[0]

def run_benchmarks(args, config: dict):
    """Run all or filtered benchmarks across target interpreters."""
    engine = DiscoveryEngine(tests_dir="benchmarks")
    includes = getattr(args, "include", None)
    excludes = getattr(args, "exclude", None)
    
    benchmarks = engine.discover(includes=includes, excludes=excludes, pattern="*.bench.json")
    if not benchmarks:
        print("No benchmarks found.", file=sys.stderr)
        return 1
        
    # Determine target interpreters
    interpreters_in_config = config.get("interpreters", {})
    if getattr(args, "interpreter", None):
        target_ints = [name.strip() for name in args.interpreter.split(",")]
    else:
        # Default to all configured interpreters
        target_ints = list(interpreters_in_config.keys())
        
    # Filter targets checking if they exist in config
    valid_targets = []
    for int_name in target_ints:
        if int_name in interpreters_in_config:
            valid_targets.append(int_name)
        else:
            logger.warning(f"Interpreter '{int_name}' not defined in config, skipping.")
            
    if not valid_targets:
        print("No valid target interpreters selected. Exiting.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Discovered {len(benchmarks)} benchmarks. Running on: {', '.join(valid_targets)}")
    print("=" * 60)
    
    runner = Runner(config)
    results = []
    failed = skipped = 0
    
    for bench in benchmarks:
        print(f"\nBenchmark: {bench.name} ({bench.id}) [Revision: {bench.config.get('revision', 1)}]")
        for int_name in valid_targets:
            int_spec = config["interpreters"][int_name]
            required_caps = bench.config.get("capabilities", [])
            provided_caps = int_spec.get("capabilities", [])
            
            # Check capabilities
            missing = [cap for cap in required_caps if cap not in provided_caps]
            if missing:
                skipped += 1
                print(f"  {int_name:18}: SKIPPED (missing: {', '.join(missing)})")
                continue
                
            print(f"  {int_name:18}: running baseline & {args.runs} measurements...", end="", flush=True)
            try:
                res = runner.run_benchmark(bench, int_name, runs=args.runs, timeout=args.timeout)
                if not res["verified"]:
                    failed += 1
                    print(" FAILED VERIFICATION")
                    print(f"    Stdout: {res['stdout'].strip()}")
                    print(f"    Stderr: {res['stderr'].strip()}")
                else:
                    results.append(res)
                    print(f" OK (Overhead: {res['overhead_ms']:.1f}ms | Raw Median: {res['median_warm_raw_ms']:.1f}ms | Adj Median: {res['median_warm_adjusted_ms']:.1f}ms)")
            except Exception as e:
                failed += 1
                print(f" ERROR: {e}")
                
    if not results:
        print("\nNo benchmarks successfully ran and verified.")
        return max(1, failed)
        
    # Group results by interpreter and save them
    env_metadata = get_system_metadata()
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    # Save a run file for each interpreter containing its results
    interpreters_run = set(r["interpreter"] for r in results)
    for int_name in interpreters_run:
        int_results = [r for r in results if r["interpreter"] == int_name]
        run_data = {
            "schema_version": 1,
            "timestamp": timestamp,
            "environment": env_metadata,
            "results": int_results
        }
        save_benchmark_run(run_data)
        
    # Print consolidated execution summary table
    print("\n" + "=" * 80)
    print("BENCHMARK EXECUTION SUMMARY (Warm Medians in ms)")
    print("=" * 80)
    print(f"{'Benchmark ID':28} | {'Interpreter':18} | {'Version':8} | {'Raw Time':10} | {'Adjusted Time':13} |")
    print("-" * 80)
    for r in results:
        print(f"{r['benchmark']:28} | {r['interpreter']:18} | {r['interpreter_version']:8} | {r['median_warm_raw_ms']:8.1f}ms | {r['median_warm_adjusted_ms']:11.1f}ms |")
    print("=" * 80)
    print(f"{len(results)} passed, {failed} failed, {skipped} skipped")
    return failed

def compare_benchmarks(args, config: dict):
    """Compare two benchmark runs to identify regression/speedups."""
    candidate = args.interpreter
    if candidate and ("," in candidate or candidate not in config.get("interpreters", {})):
        print("Comparison requires one configured candidate interpreter.", file=sys.stderr)
        return 1
    baseline_interpreter = (getattr(args, "baseline_interpreter", None) or candidate
                            or config.get("defaults", {}).get("interpreter"))
    vs_target = args.vs
    if vs_target == "latest":
        if not baseline_interpreter:
            print("Select an interpreter for the latest baseline.", file=sys.stderr)
            return 1
        vs_file = get_latest_run_file(baseline_interpreter)
        if vs_file is None:
            print(f"No history for interpreter '{baseline_interpreter}'.", file=sys.stderr)
            return 1
    else:
        vs_file = Path(vs_target)

    if not vs_file.exists():
        # Try finding in the history dir
        for p in HISTORY_DIR.rglob(vs_target):
            vs_file = p
            break
            
    if not vs_file.exists():
        print(f"Comparison run file not found: {args.vs}", file=sys.stderr)
        sys.exit(1)
        
    # Perform new run to compare against
    # First discover what benchmarks exist in the comparison run
    with open(vs_file, "r") as f:
        vs_data = json.load(f)
        
    vs_results = {r["benchmark"]: r for r in vs_data.get("results", [])}
    if not vs_results:
        print("Comparison run file contains no results.", file=sys.stderr)
        sys.exit(1)
        
    # Discover and run benchmarks matching the target interpreter of the vs file
    target_int = candidate or vs_data["results"][0]["interpreter"]
    if target_int not in config.get("interpreters", {}):
        print(f"Interpreter {target_int!r} is not configured.", file=sys.stderr)
        return 1
    print(f"Comparing {target_int!r} against {vs_data['results'][0]['interpreter']!r} version {vs_data['results'][0]['interpreter_version']}")
    print(f"Baseline run date: {vs_data['timestamp']}")
    print("=" * 80)
    
    # Run benchmarks for target interpreter
    engine = DiscoveryEngine(tests_dir="benchmarks")
    benchmarks = engine.discover(includes=args.include, excludes=args.exclude, pattern="*.bench.json")
    runner = Runner(config)
    
    print(f"{'Benchmark':28} | {'Base (Adjusted)':16} | {'New (Adjusted)':15} | {'Diff':10} | {'Verdict':8} |")
    print("-" * 80)
    
    regressions_detected = False
    compared = failed = skipped = 0
    
    for bench in benchmarks:
        if bench.id not in vs_results:
            skipped += 1
            continue
            
        base_res = vs_results[bench.id]
        
        # Verify revisions match
        curr_revision = bench.config.get("revision", 1)
        base_revision = base_res.get("benchmark_revision", 1)
        if curr_revision != base_revision:
            skipped += 1
            print(f"{bench.id:28} | [WARNING: Revision mismatch! Base: r{base_revision} vs Current: r{curr_revision}]")
            continue
            
        missing = set(bench.config.get("capabilities", [])) - set(config["interpreters"][target_int].get("capabilities", []))
        if missing:
            skipped += 1
            print(f"{bench.id:28} | SKIPPED (missing: {', '.join(sorted(missing))})")
            continue

        # Run benchmark
        try:
            res = runner.run_benchmark(bench, target_int, runs=args.runs, timeout=args.timeout)
            if not res["verified"]:
                failed += 1
                print(f"{bench.id:28} | Verification Failed on new run")
                continue
                
            baseline_hash = base_res.get("rom_sha256")
            if baseline_hash and baseline_hash != res.get("rom_sha256"):
                failed += 1
                print(f"{bench.id:28} | ROM hash mismatch; cannot compare different programs")
                continue
            if not baseline_hash:
                print(f"{bench.id:28} | WARNING: legacy baseline has no ROM hash")
            compared += 1
            base_ms = base_res["median_warm_adjusted_ms"]
            new_ms = res["median_warm_adjusted_ms"]
            
            if base_ms > 0:
                pct_diff = ((new_ms - base_ms) / base_ms) * 100.0
            else:
                pct_diff = 0.0
                
            # If performance degrades by more than 5%, flag regression
            if pct_diff > 5.0:
                verdict = "SLOWDOWN"
                regressions_detected = True
            elif pct_diff < -5.0:
                verdict = "SPEEDUP"
            else:
                verdict = "STABLE"
                
            diff_str = f"{pct_diff:+.1f}%"
            print(f"{bench.id:28} | {base_ms:12.1f}ms | {new_ms:11.1f}ms | {diff_str:10} | {verdict:8} |")
        except Exception as e:
            failed += 1
            print(f"{bench.id:28} | ERROR: {e}")
            
    print("=" * 80)
    print(f"{compared} compared, {failed} failed, {skipped} skipped")
    if failed or not compared:
        print("Comparison incomplete: failures occurred or no benchmarks were compared.")
    elif regressions_detected:
        print("\n[WARNING] Regressions detected (>5% slowdown).")
    else:
        print("\nAll compared benchmarks are stable or faster.")
    return max(1, failed) if failed or not compared else 0

def show_history(args, config: dict):
    """Display historical performance logs for a single interpreter across its versions."""
    interpreter = args.interpreter
    int_dir = HISTORY_DIR / interpreter
    
    if not int_dir.exists():
        print(f"No benchmark logs found for interpreter '{interpreter}'.", file=sys.stderr)
        sys.exit(0)
        
    all_runs = []
    for path in int_dir.glob("*.json"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
                all_runs.append(data)
        except Exception:
            pass
            
    if not all_runs:
        print(f"No valid logs found for interpreter '{interpreter}'.", file=sys.stderr)
        sys.exit(0)
        
    # Sort runs chronologically by timestamp
    all_runs.sort(key=lambda r: r["timestamp"])
    
    print("=" * 85)
    print(f"HISTORICAL PERFORMANCE LOGS FOR: {interpreter.upper()}")
    print("=" * 85)
    
    # Discover all benchmark names tracked in these logs
    benchmarks_tracked = set()
    for run in all_runs:
        for r in run.get("results", []):
            benchmarks_tracked.add((r["benchmark"], r.get("benchmark_revision", 1)))
            
    benchmarks_tracked = sorted(list(benchmarks_tracked))
    
    # Print timeline columns
    headers = [f"{run['results'][0]['interpreter_version']} ({run['timestamp'][:10]})" for run in all_runs]
    print(f"{'Benchmark ID (Rev)':28} | " + " | ".join(f"{h:18}" for h in headers) + " |")
    print("-" * 85)
    
    for bench_id, rev in benchmarks_tracked:
        row = f"{bench_id} (r{rev})".ljust(28) + " | "
        times = []
        for run in all_runs:
            match = None
            for r in run.get("results", []):
                if r["benchmark"] == bench_id and r.get("benchmark_revision", 1) == rev:
                    match = r
                    break
            if match:
                times.append(f"{match['median_warm_adjusted_ms']:13.1f}ms")
            else:
                times.append("N/A".center(15))
        row += " | ".join(times) + " |"
        print(row)
        
    print("=" * 85)
