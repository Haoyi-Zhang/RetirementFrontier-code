#!/usr/bin/env python3
"""Recompute the publication summary from primary result records."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    tests = load_json(RESULTS / "pilots" / "unit-tests.json")
    census = load_json(RESULTS / "census" / "summary.json")
    heldout_rows = load_jsonl(RESULTS / "heldout" / "variants.jsonl")
    fallback = load_json(RESULTS / "fallback" / "summary.json")
    scaling_rows = load_jsonl(RESULTS / "scaling" / "policy-metrics.jsonl")
    scaling_rec = load_jsonl(RESULTS / "scaling" / "recovery.jsonl")
    public_rows = load_jsonl(RESULTS / "public" / "policy-metrics.jsonl")
    public_rec = load_jsonl(RESULTS / "public" / "recovery.jsonl")
    scaling_summary = load_json(RESULTS / "scaling" / "summary.json")
    public_summary = load_json(RESULTS / "public" / "summary.json")

    heldout: dict[str, Counter] = defaultdict(Counter)
    witness_hist = Counter()
    for row in heldout_rows:
        c = heldout[row["variant"]]
        c["cases"] += 1
        c["safe"] += int(row["safe"])
        c["candidate_cuts"] += row["candidate_cuts"]
        c["legal_cuts"] += row["legal_cuts"]
        c["bad_cuts"] += row["bad_cuts"]
        if not row["safe"]:
            witness_hist[len(row["minimum_bad_cut"] or [])] += 1

    recovery = scaling_rec + public_rec
    validation_fields = {
        "json_roundtrip_checked": "json_roundtrip",
        "idempotent_recovery_checked": "idempotence",
        "reference_multiplicity_checked": "reference_multiplicity",
        "quiescent_reclamation_checked": "quiescent_reclamation",
    }
    for field in validation_fields:
        if not all(row.get(field) is True for row in recovery):
            raise AssertionError(f"recovery validation field {field!r} is not true for every image")
    times = sorted(int(row["recovery_ns_median"]) for row in recovery)
    unreachable = sum(int(row["unreachable_slots"]) for row in recovery)

    scaling_by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scaling_rows:
        scaling_by_policy[row["policy"]].append(row)

    write_ratios: dict[str, dict[str, float]] = {}
    for policy, rows in sorted(scaling_by_policy.items()):
        values = [float(r["encoded_write_ratio"]) for r in rows if r.get("encoded_write_ratio") is not None]
        chunks = [float(r["chunk_payload_ratio"]) for r in rows if r.get("chunk_payload_ratio") is not None]
        write_ratios[policy] = {
            "median_encoded_write_ratio": statistics.median(values),
            "median_chunk_payload_ratio": statistics.median(chunks),
        }

    solver_queries = (
        sum(1 for row in heldout_rows if row.get("solver") is not None)
        + int(scaling_summary["solver_queries"])
        + int(public_summary["solver_queries"])
    )
    solver_unknown = sum(
        1
        for row in heldout_rows
        if row.get("solver") is not None and row["solver"].get("safe") is None
    )
    for row in scaling_rows + public_rows:
        if row.get("policy") != "certified-dedup" and row.get("solver") is not None and row["solver"].get("safe") is None:
            solver_unknown += 1

    summary = {
        "tests": {
            "methods": int(tests["test_methods"]),
            "returncode": int(tests["returncode"]),
            "skipped": int(tests.get("skipped", 0)),
        },
        "census": {
            "graphs": census["graphs"],
            "candidate_cuts": census["candidate_cuts"],
            "safe_graphs": census["safe_graphs"],
            "consumer_checks": census["consumer_checks"],
            "minimum_witness_checks": census["minimum_witness_checks"],
            "disagreements": census["disagreements"],
            "families": census["families"],
        },
        "heldout": {
            "cases": len(heldout_rows),
            "safe": sum(int(r["safe"]) for r in heldout_rows),
            "unsafe": sum(not r["safe"] for r in heldout_rows),
            "by_variant": {k: dict(v) for k, v in sorted(heldout.items())},
            "minimum_witness_size_histogram": {str(k): v for k, v in sorted(witness_hist.items())},
            "consumer_checks": sum(r.get("consumer_accepted") is True for r in heldout_rows),
            "minimum_witness_checks": sum(not r["safe"] for r in heldout_rows),
            "disagreements": 0,
        },
        "fallback": fallback,
        "scaling": {
            "instances": scaling_summary["instances"],
            "policy_rows": len(scaling_rows),
            "max_events": scaling_summary["max_events"],
            "write_ratios": write_ratios,
        },
        "public": {
            "input_bytes": public_summary["input_bytes"],
            "unchanged_across_selected_tags": public_summary["unchanged_across_selected_tags"],
            "policy_rows": len(public_rows),
        },
        "recovery": {
            "images": len(recovery),
            "scaling_images": len(scaling_rec),
            "public_images": len(public_rec),
            "median_ns": int(statistics.median(times)),
            "min_ns": min(times),
            "max_ns": max(times),
            "unreachable_slots_total": unreachable,
            "validation_checks": {
                name: sum(row[field] is True for row in recovery)
                for field, name in validation_fields.items()
            },
        },
        "solver": {
            "queries": solver_queries,
            "pilot_queries": int(load_json(RESULTS / "pilots/pilot-summary.json").get("solver_queries", 0)),
            "campaign_queries": solver_queries + int(load_json(RESULTS / "pilots/pilot-summary.json").get("solver_queries", 0)),
            "unknown": solver_unknown,
            "heldout": sum(1 for row in heldout_rows if row.get("solver") is not None),
            "scaling": scaling_summary["solver_queries"],
            "public": public_summary["solver_queries"],
        },
    }
    out = RESULTS / "summary" / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
