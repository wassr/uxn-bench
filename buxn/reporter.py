import datetime
from rich.console import Console
from rich.tree import Tree
from rich.live import Live
from typing import List, Dict

from .discovery import TestNode
from .verifier import Verifier, VerificationResult
from .runner import Runner, ExecutionResult

console = Console()

class ExecutionReporter:
    def __init__(self, tests: List[TestNode], config: dict, quiet=False, verbose=False, plain=False, log_file="buxn.log", fail_fast=False, timeout=2.0):
        self.tests = tests
        self.runner = Runner(config)
        self.verifier = Verifier()
        self.quiet = quiet
        self.verbose = verbose
        self.plain = plain
        self.log_file = log_file
        self.fail_fast = fail_fast
        self.timeout = timeout
        
        self.exec_results = {}
        self.verif_results = {}
        
        # Resolve capabilities from config
        defaults = config.get("defaults", {})
        asm_name = defaults.get("assembler", "uxnasm")
        int_name = defaults.get("interpreter", "uxncli")
        
        self.asm_capabilities = set(config.get("assemblers", {}).get(asm_name, {}).get("capabilities", []))
        self.int_capabilities = set(config.get("interpreters", {}).get(int_name, {}).get("capabilities", []))
        self.available_capabilities = self.asm_capabilities.union(self.int_capabilities)
        
    def _is_test_supported(self, test: TestNode) -> tuple:
        req = test.config.get("capabilities")
        if req is None:
            ttype = test.config.get("type", "assembler")
            if ttype == "assembler":
                req = ["assembler"]
            else:
                req = ["cpu"]
        unsupported = [cap for cap in req if cap not in self.available_capabilities]
        if unsupported:
            return False, unsupported
        return True, []
        
    def run_all(self):
        # Open log file
        with open(self.log_file, "a") as lf:
            lf.write(f"\n--- Execution Run: {datetime.datetime.now().isoformat()} ---\n")
            
            # If quiet mode, live tree is too noisy. Fallback to plain which is silent on success.
            if self.plain or self.quiet:
                self._run_plain(lf)
            else:
                self._run_live(lf)
                
    def _run_plain(self, lf):
        total_succeeded = 0
        total_failed = 0
        total_skipped = 0
        total_duration = 0.0
        
        if not self.quiet:
            console.print("[bold blue]uxn-bench tests (Plain Mode)[/bold blue]")
        
        for test in self.tests:
            supported, missing = self._is_test_supported(test)
            if not supported:
                total_skipped += 1
                if not self.quiet:
                    missing_str = ", ".join(missing)
                    console.print(f"[yellow][SKIP][/yellow] {test.id} [dim](requires: {missing_str})[/dim]")
                lf.write(f"TEST: {test.id} | Skipped | Missing: {missing}\n")
                continue
                
            # Execute
            e_res = self.runner.run_test(test, timeout=self.timeout)
            v_res = self.verifier.verify(test, e_res)
            
            self.exec_results[test.id] = e_res
            self.verif_results[test.id] = v_res
            
            passed = v_res.passed
            if passed: total_succeeded += 1
            else: total_failed += 1
            total_duration += e_res.duration_ms
            
            # Log
            self._log_test(lf, test, e_res, v_res)
            
            # Print plain line
            if self.quiet and passed:
                continue
                
            color = "green" if passed else "red"
            symbol = "PASS" if passed else "FAIL"
            console.print(f"[{color}][{symbol}][/{color}] {test.id} [dim]({e_res.duration_ms:.1f}ms)[/dim]")
            
            if self.verbose or not passed:
                for rule in v_res.rules:
                    if self.verbose or not rule.passed:
                        r_color = "green" if rule.passed else "red"
                        r_sym = "+" if rule.passed else "-"
                        console.print(f"  [{r_color}]{r_sym} {rule.name}[/{r_color}] [dim]{rule.details}[/dim]")
                        
            if self.fail_fast and not passed:
                if not self.quiet: console.print("[yellow]Fail-fast triggered. Stopping.[/yellow]")
                break
                
        self._print_summary(total_succeeded, total_failed, total_skipped, total_duration)
 
    def _run_live(self, lf):
        # Build initial tree
        tree = Tree("[bold blue]uxn-bench tests[/bold blue]")
        groups = {}
        node_map = {} # Maps test.id to its rich Tree node
        group_nodes = {"": tree} # Maps group path to rich Tree node
        
        for test in self.tests:
            if test.group not in groups:
                groups[test.group] = []
            groups[test.group].append(test)
            
        for group_name in sorted(groups.keys()):
            parts = group_name.split("/")
            current_path = ""
            
            for part in parts:
                if not part or part == ".": continue
                parent_path = current_path
                current_path = f"{current_path}/{part}" if current_path else part
                
                if current_path not in group_nodes:
                    parent_node = group_nodes[parent_path]
                    group_nodes[current_path] = parent_node.add(f"[bold cyan]{part}[/bold cyan]")
                
            for test in groups[group_name]:
                name_part = test.id.split("/")[-1]
                t_node = group_nodes[group_name].add(f"[dim][WAIT][/dim] {name_part}")
                node_map[test.id] = t_node
                
        total_succeeded = 0
        total_failed = 0
        total_skipped = 0
        total_duration = 0.0
        
        with Live(tree, console=console, refresh_per_second=10) as live:
            for test in self.tests:
                name_part = test.id.split("/")[-1]
                
                supported, missing = self._is_test_supported(test)
                if not supported:
                    total_skipped += 1
                    node_map[test.id].label = f"[yellow][SKIP][/yellow] {name_part} [dim](missing: {', '.join(missing)})[/dim]"
                    live.refresh()
                    lf.write(f"TEST: {test.id} | Skipped | Missing: {missing}\n")
                    continue
                    
                # Update UI to running
                node_map[test.id].label = f"[yellow][RUN ][/yellow] {name_part}"
                live.refresh()
                
                # Execute
                e_res = self.runner.run_test(test, timeout=self.timeout)
                v_res = self.verifier.verify(test, e_res)
                
                self.exec_results[test.id] = e_res
                self.verif_results[test.id] = v_res
                
                passed = v_res.passed
                if passed: total_succeeded += 1
                else: total_failed += 1
                total_duration += e_res.duration_ms
                
                # Log
                self._log_test(lf, test, e_res, v_res)
                
                # Update UI to finished
                color = "green" if passed else "red"
                symbol = "PASS" if passed else "FAIL"
                
                node_map[test.id].label = f"[{color}][{symbol}][/{color}] {name_part} [dim]({e_res.duration_ms:.1f}ms)[/dim]"
                if self.verbose or not passed:
                    for rule in v_res.rules:
                        if self.verbose or not rule.passed:
                            r_color = "green" if rule.passed else "red"
                            r_sym = "+" if rule.passed else "-"
                            node_map[test.id].add(f"[{r_color}]{r_sym} {rule.name}[/{r_color}] [dim]{rule.details}[/dim]")
                                
                if self.fail_fast and not passed:
                    break
                    
        self._print_summary(total_succeeded, total_failed, total_skipped, total_duration)
 
    def _log_test(self, lf, test, e_res, v_res):
        passed = v_res.passed
        lf.write(f"TEST: {test.id} | Passed: {passed} | Duration: {e_res.duration_ms:.2f}ms\n")
        if not passed:
            lf.write(f"  STDERR:\n{e_res.stderr}\n")
        for rule in v_res.rules:
            lf.write(f"  RULE: {rule.name} | Passed: {rule.passed} | {rule.details}\n")
 
    def _print_summary(self, succeeded, failed, skipped, duration):
        total_run = succeeded + failed
        s_color = "green" if failed == 0 else "red"
        
        summary = (
            f"[{s_color}]{len(self.tests)} found, {total_run} run, "
            f"{succeeded} pass, {failed} fail, {skipped} skip "
            f"in {duration:.1f}ms[/{s_color}] [dim]({self.log_file})[/dim]"
        )
        
        console.print()
        if self.quiet and failed == 0:
            console.print(f"[green]All {total_run} tests passed[/green] [dim]({skipped} skipped) in {duration:.1f}ms ({self.log_file})[/dim]")
        else:
            console.print(summary)
