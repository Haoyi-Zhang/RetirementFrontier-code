#!/usr/bin/env python3
"""Run the full seven-stage experiment with optional C-library SMT checks.

--without-smt executes every core stage while explicitly omitting solver calls.
--require-smt probes the actual C API and fails before starting if unavailable.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from solver import solver_available


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--require-smt", action="store_true")
    modes.add_argument("--without-smt", action="store_true")
    args = parser.parse_args()
    available = False if args.without_smt else solver_available()
    if args.require_smt and not available:
        print("A usable Z3 C shared library is required but unavailable.", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["RF_SKIP_SMT"] = "0" if available else "1"
    record = {"mode": "full" if available else "core-without-smt",
              "smt_executed": available,
              "omitted_checks": [] if available else ["SMT calls within pilots, heldout, scaling, public"],
              "all_core_stages_requested": True}
    cp = subprocess.run([sys.executable, str(ROOT / "scripts/reproduce.py"), "--stage", "all"],
                        cwd=ROOT, env=env, check=False)
    record["returncode"] = cp.returncode
    out = ROOT / "results/summary/execution-mode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return cp.returncode


if __name__ == "__main__":
    raise SystemExit(main())
