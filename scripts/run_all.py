#!/usr/bin/env python3
"""Lightweight fresh-process orchestrator for the complete campaign."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
REPRODUCE = REPO / "scripts" / "reproduce.py"
STAGES = ("tests", "pilots", "census", "heldout", "fallback", "scaling", "public")
RESULT_PATHS = {
    "tests": RESULTS / "pilots" / "unit-tests.json",
    "pilots": RESULTS / "pilots" / "pilot-summary.json",
    "census": RESULTS / "census" / "summary.json",
    "heldout": RESULTS / "heldout" / "summary.json",
    "fallback": RESULTS / "fallback" / "summary.json",
    "scaling": RESULTS / "scaling" / "summary.json",
    "public": RESULTS / "public" / "summary.json",
}


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    """Run each stage in a clean interpreter and compose one campaign record."""
    start = time.perf_counter()
    completed: list[dict[str, Any]] = []
    for stage in STAGES:
        print(f"[reproduce] {stage} (fresh process)", flush=True)
        proc = subprocess.run(
            [sys.executable, str(REPRODUCE), "--stage", stage],
            cwd=REPO,
            timeout=45 * 60,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"campaign stage {stage!r} failed with status {proc.returncode}")
        path = RESULT_PATHS[stage]
        if not path.is_file():
            raise RuntimeError(f"campaign stage {stage!r} did not create {path}")
        completed.append(json.loads(path.read_text(encoding="utf-8")))

    total_cpu = sum(float(row.get("cpu_seconds", 0.0)) for row in completed)
    if total_cpu > 8 * 3600:
        raise RuntimeError("campaign exceeded the declared eight CPU-hour ceiling")
    record = {
        "requested_stage": "all",
        "execution_mode": "fresh-process-per-stage",
        "completed": completed,
        "wall_seconds": time.perf_counter() - start,
        "cpu_seconds": total_cpu,
        "max_rss_kib": max((int(row.get("max_rss_kib", 0)) for row in completed), default=0),
    }
    _save_json(RESULTS / "campaign-run.json", record)
    print(json.dumps(record, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
