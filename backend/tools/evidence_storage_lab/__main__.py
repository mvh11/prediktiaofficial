"""Explicit init / correctness gate / staged benchmark commands, no multi-million option."""

import argparse
import json
from pathlib import Path

from tools.evidence_storage_lab.safety import Target, environment, sources, write_report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="LAB-only fixture evidence architecture; local disposable PostgreSQL")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "gate", "bench"):
        command = sub.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        if name == "bench":
            command.add_argument("--observations", type=int, choices=(100_000, 1_000_000), required=True)
            command.add_argument("--samples", type=int, default=15)
            command.add_argument("--gate-report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "bench" and args.samples < 10:
        parser.error("At least ten measured samples required")
    if args.output.exists():
        parser.error("Reports are immutable; choose a new output path")
    return args


def main(argv=None):
    args = parse_args(argv)
    target = Target.from_environment()
    before = sources()
    target.configure_app()
    if args.command == "init":
        from tools.evidence_storage_lab.dataset import initialize
        result = initialize(target)
    elif args.command == "gate":
        from tools.evidence_storage_lab.semantics import run_gate
        result = run_gate(target)
        from tools.evidence_storage_lab.recovery import backup_restore_gate
        result["backup_restore"] = backup_restore_gate(target, args.output.parent)
    else:
        gate = json.loads(args.gate_report.read_text(encoding="utf-8"))
        if gate["result"]["status"] != "PASS" or not gate["source_unchanged"]:
            raise RuntimeError("Large benchmarks require a passing correctness gate")
        if gate["source_sha256"] != before:
            raise RuntimeError("Sources changed since correctness gate; gate must be rerun")
        from tools.evidence_storage_lab.benchmark import benchmark
        result = benchmark(target, args.observations, args.samples, args.output.parent)
    unchanged = before == sources()
    report = {"command": args.command, "environment": environment(target), "source_sha256": before,
              "source_unchanged": unchanged, "result": result}
    write_report(args.output, report)
    print(f"Report: {args.output}; sources unchanged={unchanged}", flush=True)
    if not unchanged:
        raise RuntimeError("Source changes during execution: evidence not comparable")


if __name__ == "__main__":
    main()
