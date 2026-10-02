#!/usr/bin/env python3
"""Run the bounded, single-worker retirement-frontier campaign."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import random
import re
import resource
import statistics
import subprocess
import sys
import time
from itertools import combinations, product
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from checker import analyze_model, pair_analyze_model  # noqa: E402
from engine import (  # noqa: E402
    build_trace,
    census_template,
    edge_free_trace,
    heldout_operations,
    public_replay_operations,
    scaling_operations,
    strip_build_metrics,
    unanchor_first_ack,
    weaken_trace,
)
from fallback import (  # noqa: E402
    assignment_cut,
    evaluates_formula,
    fixed_candidate_model,
    formula_trace,
    sign_pattern_clauses,
)
from medium import erase_slots, materialize, recover  # noqa: E402
from model import admit_trace, save_json  # noqa: E402
from oracle import exhaustive_model  # noqa: E402
from solver import SolverUnavailable, solve_model  # noqa: E402
from verifier import verify_certificate  # noqa: E402

RESULTS = REPO / "results"
INPUTS = REPO / "inputs"
USE_SMT = os.environ.get("RF_SKIP_SMT") != "1"


def _mkdirs() -> None:
    for rel in [
        "pilots", "census", "heldout", "fallback", "scaling", "public", "summary"
    ]:
        (RESULTS / rel).mkdir(parents=True, exist_ok=True)
    for rel in ["heldout", "scaling", "sqlite"]:
        (INPUTS / rel).mkdir(parents=True, exist_ok=True)


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _limits() -> None:
    try:
        affinity = sorted(os.sched_getaffinity(0))[:4]
        if affinity:
            os.sched_setaffinity(0, affinity)
    except (AttributeError, OSError):
        pass
    # 3.5 GiB address-space ceiling and an 8 CPU-hour campaign ceiling.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (int(3.5 * 1024**3), int(3.5 * 1024**3)))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (8 * 3600, 8 * 3600))
    except (ValueError, OSError):
        pass


def _resource_record(start_wall: float, start_cpu: float) -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "wall_seconds": time.perf_counter() - start_wall,
        "cpu_seconds": time.process_time() - start_cpu,
        "max_rss_kib": int(usage.ru_maxrss),
    }


def run_tests() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(REPO / "tests"), "-v"],
        text=True,
        capture_output=True,
        env=env,
        timeout=40,
        check=False,
    )
    record = {
        "stage": "tests",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "test_methods": int(re.search(r"Ran (\d+) tests?", proc.stderr + proc.stdout).group(1)),
        "skipped": (proc.stderr + proc.stdout).count(" ... skipped"),
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "pilots" / "unit-tests.json"), record)
    if proc.returncode != 0:
        raise RuntimeError("unit tests failed")
    return record


def run_pilots() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    # Four deterministic puts in two batches produce a nontrivial reuse trace.
    operations = [
        {"op": "put", "name": "a", "chunks_hex": [b"A".hex()]},
        {"op": "put", "name": "b", "chunks_hex": [b"B".hex()]},
        {"op": "put", "name": "a", "chunks_hex": [b"C".hex()]},
        {"op": "put", "name": "b", "chunks_hex": [b"D".hex()]},
    ]
    ordered = strip_build_metrics(build_trace(operations, deduplicate=True, batch_size=2))
    negative = edge_free_trace(ordered)
    save_json(str(INPUTS / "pilot-trace.json"), ordered)
    rows: list[dict[str, Any]] = []
    for label, trace in [("ordered", ordered), ("edge-free", negative)]:
        model = admit_trace(trace)
        frontier = analyze_model(model, minimum=True)
        pair = pair_analyze_model(model, minimum=True)
        exact = exhaustive_model(model, max_events=22)
        solver = solve_model(model) if USE_SMT else None
        consumer = verify_certificate(model.trace, frontier["certificate"]) if frontier["safe"] else None
        if not (frontier["safe"] == pair["safe"] == exact["safe"]):
            raise AssertionError(f"pilot disagreement for {label}")
        if solver is not None and solver["safe"] != frontier["safe"]:
            raise AssertionError(f"pilot SMT disagreement for {label}")
        if not frontier["safe"] and not (
            frontier["minimum_bad_cut"] == pair["minimum_bad_cut"] == exact["minimum_bad_cut"]
        ):
            raise AssertionError(f"pilot minimum-witness disagreement for {label}")
        rows.append({
            "label": label,
            "events": model.n,
            "frontier": frontier,
            "pair": pair,
            "oracle": exact,
            "solver": solver,
            "consumer": consumer,
        })
    _jsonl(RESULTS / "pilots" / "pilot-results.jsonl", rows)
    result = {"stage": "pilots", "cases": len(rows), "solver_queries": sum(r["solver"] is not None for r in rows), **_resource_record(start_wall, start_cpu)}
    save_json(str(RESULTS / "pilots" / "pilot-summary.json"), result)
    return result


def run_census() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for family, edge_bits in [("unretired-reuse", 10), ("retired-free", 15)]:
        counts = Counter()
        for mask in range(1 << edge_bits):
            model = admit_trace(census_template(family, mask))
            frontier = analyze_model(model, minimum=True)
            pair = pair_analyze_model(model, minimum=True)
            exact = exhaustive_model(model, max_events=6)
            if not (frontier["safe"] == pair["safe"] == exact["safe"]):
                raise AssertionError(f"census disagreement {family} mask={mask}")
            minimum_agrees = True
            consumer_accepted: bool | None = None
            if frontier["safe"]:
                consumer_accepted = bool(verify_certificate(model.trace, frontier["certificate"])["accepted"])
                if not consumer_accepted:
                    raise AssertionError(f"census certificate rejection {family} mask={mask}")
                counts["consumer_checks"] += 1
            else:
                minimum_agrees = (
                    frontier["minimum_bad_cut"]
                    == pair["minimum_bad_cut"]
                    == exact["minimum_bad_cut"]
                )
                if not minimum_agrees:
                    raise AssertionError(f"census minimum-witness disagreement {family} mask={mask}")
                counts["minimum_witness_checks"] += 1
            row = {
                "family": family,
                "edge_mask": mask,
                "safe": frontier["safe"],
                "events": model.n,
                "candidate_cuts": exact["candidate_cuts"],
                "legal_cuts": exact["legal_cuts"],
                "bad_cuts": exact["bad_cuts"],
                "minimum_bad_cut": exact["minimum_bad_cut"],
                "minimum_bad_cut_size": None if exact["minimum_bad_cut"] is None else len(exact["minimum_bad_cut"]),
                "minimum_witness_agrees": minimum_agrees,
                "consumer_accepted": consumer_accepted,
                "frontier_obligations": frontier["frontier_obligations"],
                "pair_obligations": pair["pair_obligations"],
            }
            rows.append(row)
            counts["graphs"] += 1
            counts["safe_graphs"] += int(row["safe"])
            counts["candidate_cuts"] += row["candidate_cuts"]
            counts["legal_cuts"] += row["legal_cuts"]
            counts["bad_cuts"] += row["bad_cuts"]
        summary[family] = dict(counts)
    _jsonl(RESULTS / "census" / "graphs.jsonl", rows)
    result = {
        "stage": "census",
        "families": summary,
        "graphs": sum(x["graphs"] for x in summary.values()),
        "candidate_cuts": sum(x["candidate_cuts"] for x in summary.values()),
        "safe_graphs": sum(x["safe_graphs"] for x in summary.values()),
        "consumer_checks": sum(x.get("consumer_checks", 0) for x in summary.values()),
        "minimum_witness_checks": sum(x.get("minimum_witness_checks", 0) for x in summary.values()),
        "disagreements": 0,
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "census" / "summary.json"), result)
    return result


def run_heldout() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    rows: list[dict[str, Any]] = []
    solver_queries = 0
    for seed in range(10000, 10064):
        base = build_trace(heldout_operations(seed), deduplicate=True, batch_size=2)
        variants = {
            "ordered": strip_build_metrics(base),
            "weakened": weaken_trace(base, seed=seed + 1, retention=0.65),
            "edge-free": edge_free_trace(base),
            "unanchored": unanchor_first_ack(base),
        }
        for variant, trace in variants.items():
            save_json(str(INPUTS / "heldout" / f"seed-{seed}-{variant}.json"), trace)
            model = admit_trace(trace)
            frontier = analyze_model(model, minimum=True)
            pair = pair_analyze_model(model, minimum=True)
            exact = exhaustive_model(model, max_events=22)
            if not (frontier["safe"] == pair["safe"] == exact["safe"]):
                raise AssertionError(f"held-out disagreement seed={seed} variant={variant}")
            minimum_agrees = True
            if not frontier["safe"]:
                minimum_agrees = (
                    frontier["minimum_bad_cut"]
                    == pair["minimum_bad_cut"]
                    == exact["minimum_bad_cut"]
                )
                if not minimum_agrees:
                    raise AssertionError(
                        f"held-out minimum-witness disagreement seed={seed} variant={variant}"
                    )
            solver: dict[str, Any] | None = None
            if USE_SMT and seed < 10008:
                solver = solve_model(model)
                solver_queries += 1
                if solver["safe"] != frontier["safe"]:
                    raise AssertionError("held-out solver disagreement")
            consumer = verify_certificate(model.trace, frontier["certificate"]) if frontier["safe"] else None
            rows.append({
                "seed": seed,
                "variant": variant,
                "events": model.n,
                "safe": frontier["safe"],
                "candidate_cuts": exact["candidate_cuts"],
                "legal_cuts": exact["legal_cuts"],
                "bad_cuts": exact["bad_cuts"],
                "minimum_bad_cut": frontier["minimum_bad_cut"],
                "minimum_witness_agrees": minimum_agrees,
                "consumer_accepted": None if consumer is None else bool(consumer["accepted"]),
                "frontier_obligations": frontier["frontier_obligations"],
                "pair_obligations": pair["pair_obligations"],
                "certificate_bytes": frontier["certificate_bytes"],
                "producer_cpu_seconds": frontier["producer_cpu_seconds"],
                "pair_cpu_seconds": pair["pair_cpu_seconds"],
                "oracle_cpu_seconds": exact["oracle_cpu_seconds"],
                "consumer": consumer,
                "solver": solver,
            })
    _jsonl(RESULTS / "heldout" / "variants.jsonl", rows)
    by_variant: dict[str, Counter] = defaultdict(Counter)
    witness_hist = Counter()
    for row in rows:
        c = by_variant[row["variant"]]
        c["cases"] += 1
        c["safe"] += int(row["safe"])
        c["candidate_cuts"] += row["candidate_cuts"]
        c["legal_cuts"] += row["legal_cuts"]
        c["bad_cuts"] += row["bad_cuts"]
        if not row["safe"]:
            witness_hist[len(row["minimum_bad_cut"] or [])] += 1
    result = {
        "stage": "heldout",
        "cases": len(rows),
        "safe": sum(int(r["safe"]) for r in rows),
        "unsafe": sum(not r["safe"] for r in rows),
        "solver_queries": solver_queries,
        "consumer_checks": sum(r["consumer_accepted"] is True for r in rows),
        "minimum_witness_checks": sum(not r["safe"] for r in rows),
        "by_variant": {k: dict(v) for k, v in sorted(by_variant.items())},
        "minimum_witness_size_histogram": {str(k): v for k, v in sorted(witness_hist.items())},
        "disagreements": 0,
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "heldout" / "summary.json"), result)
    return result


def run_fallback() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    patterns = sign_pattern_clauses()
    truth_rows: list[dict[str, Any]] = []
    fixed_rows: list[dict[str, Any]] = []
    for mask in range(1, 1 << len(patterns)):
        clauses = [patterns[i] for i in range(len(patterns)) if mask & (1 << i)]
        model = admit_trace(formula_trace(clauses))
        formula_sat = False
        for assignment in product([False, True], repeat=3):
            expected = evaluates_formula(clauses, assignment)
            formula_sat |= expected
            cut = assignment_cut(model, assignment)
            outcome = recover(materialize(model, cut), fallback=True)
            observed = int(outcome["prefix"]) < 1
            if observed != expected:
                raise AssertionError(f"fallback reduction mismatch mask={mask} assignment={assignment}")
            truth_rows.append({
                "formula_mask": mask,
                "clauses": len(clauses),
                "assignment": [int(x) for x in assignment],
                "satisfies": expected,
                "fallback_ack_violation": observed,
                "cut_size": len(cut),
            })
        if len(clauses) <= 3:
            fixed = fixed_candidate_model(model, max_choices=1_000_000)
            exact = exhaustive_model(model, fallback=True, max_events=22)
            expected_safe = not formula_sat
            if fixed["safe"] != expected_safe or exact["safe"] != expected_safe:
                raise AssertionError(f"fixed-candidate safety mismatch mask={mask}")
            minimum_agrees = fixed["minimum_bad_cut"] == exact["minimum_bad_cut"]
            if not minimum_agrees:
                raise AssertionError(f"fixed-candidate minimum-witness mismatch mask={mask}")
            fixed_rows.append({
                "formula_mask": mask,
                "clauses": len(clauses),
                "formula_satisfiable": formula_sat,
                "oracle_candidate_cuts": exact["candidate_cuts"],
                "oracle_legal_cuts": exact["legal_cuts"],
                "oracle_bad_cuts": exact["bad_cuts"],
                "oracle_minimum_bad_cut": exact["minimum_bad_cut"],
                "minimum_witness_agrees": minimum_agrees,
                "oracle_cpu_seconds": exact["oracle_cpu_seconds"],
                **fixed,
            })
    _jsonl(RESULTS / "fallback" / "truth-table.jsonl", truth_rows)
    _jsonl(RESULTS / "fallback" / "fixed-candidate.jsonl", fixed_rows)
    result = {
        "stage": "fallback",
        "formulas": (1 << len(patterns)) - 1,
        "assignments": len(truth_rows),
        "fixed_candidate_formulas": len(fixed_rows),
        "fixed_candidate_oracle_checks": len(fixed_rows),
        "minimum_witness_checks": sum(not row["safe"] for row in fixed_rows),
        "disagreements": 0,
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "fallback" / "summary.json"), result)
    return result


def _sample_cuts(model, count: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    full = set(range(model.n))
    cuts: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    candidates: list[list[int]] = [[]]
    candidates += [[root] for root in model.roots]
    candidates += [[i] for i in range(0, model.n, max(1, model.n // max(1, count)))]
    attempts = 0
    while len(candidates) < count * 8 and attempts < count * 20:
        attempts += 1
        width = 1 + rng.randrange(min(8, max(1, model.n)))
        candidates.append([rng.randrange(model.n) for _ in range(width)])
    for seeds in candidates:
        cut = tuple(sorted(model.closure_ids(seeds)))
        if set(cut) == full or cut in seen:
            continue
        seen.add(cut)
        cuts.append(list(cut))
        if len(cuts) == count:
            break
    if len(cuts) != count:
        raise RuntimeError(f"could not construct {count} distinct legal cuts")
    return cuts


def _expected_reference_counts(model, root_event: int | None) -> dict[str, int]:
    if root_event is None:
        return {}
    counts: Counter[str] = Counter()
    namespace = model.events[root_event]["namespace"]
    for name in sorted(namespace):
        for ref in namespace[name]:
            counts[":".join(map(str, (
                int(ref["slot"]),
                int(ref["generation"]),
                str(ref["digest"]),
                int(ref["length"]),
            )))] += 1
    return dict(counts)


def _timed_recovery(model, cut: list[int], repeats: int = 7) -> dict[str, Any]:
    # Round-trip through the documented JSON image wire format before recovery.
    image = json.loads(json.dumps(materialize(model, cut), sort_keys=True, separators=(",", ":")))
    values: list[int] = []
    outcomes: list[dict[str, Any]] = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        outcome = recover(image)
        values.append(time.perf_counter_ns() - start)
        outcomes.append(outcome)
    first = outcomes[0]
    if not first.get("ok"):
        raise AssertionError(f"engine-generated legal cut failed recovery: {first}")
    if any(outcome != first for outcome in outcomes[1:]):
        raise AssertionError("read-only recovery was not idempotent")

    root_event = first["root_event"]
    expected_counts = _expected_reference_counts(model, root_event)
    if first["reference_counts"] != expected_counts:
        raise AssertionError("recovered reference multiplicities differ from the selected manifest")

    # Quiescent reclamation check: remove every occupied slot unreachable from
    # the selected strict root and require the same logical recovery result.
    reclaimed_image = erase_slots(image, first["unreachable_slots"])
    reclaimed_image = json.loads(json.dumps(reclaimed_image, sort_keys=True, separators=(",", ":")))
    reclaimed = recover(reclaimed_image)
    logical_fields = ("ok", "prefix", "root_event", "objects_hex", "reference_counts")
    if any(reclaimed.get(field) != first.get(field) for field in logical_fields):
        raise AssertionError("erasing unreachable occupied slots changed strict recovery")

    return {
        "events_in_cut": len(set(cut)),
        "prefix": int(first["prefix"]),
        "root_event": root_event,
        "root_history": int(first["root_history"]),
        "reachable_slots": len(first["reachable_slots"]),
        "unreachable_slots": len(first["unreachable_slots"]),
        "json_roundtrip_checked": True,
        "idempotent_recovery_checked": True,
        "reference_multiplicity_checked": True,
        "quiescent_reclamation_checked": True,
        "recovery_ns_median": int(statistics.median(values)),
        "recovery_ns_min": min(values),
        "recovery_ns_max": max(values),
    }


def _analyze_policy(trace: dict[str, Any], *, label: str, pair_solver: bool) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    metrics = dict(trace.get("build_metrics", {}))
    model = admit_trace(strip_build_metrics(trace))
    frontier = analyze_model(model)
    if not frontier["safe"]:
        raise AssertionError(f"generated policy {label} is unsafe")
    consumer_start = time.process_time()
    consumer = verify_certificate(model.trace, frontier["certificate"])
    consumer_cpu = time.process_time() - consumer_start
    pair = pair_analyze_model(model) if pair_solver else None
    solver = solve_model(model) if pair_solver and USE_SMT else None
    if pair is not None and pair["safe"] != frontier["safe"]:
        raise AssertionError("pair disagreement")
    if solver is not None and solver["safe"] != frontier["safe"]:
        raise AssertionError("solver disagreement")
    row = {
        "policy": label,
        "events": model.n,
        "roots": len(model.roots),
        "slots": len(model.writers_by_slot),
        "frontier_obligations": frontier["frontier_obligations"],
        "certificate_bytes": frontier["certificate_bytes"],
        "producer_cpu_seconds": frontier["producer_cpu_seconds"],
        "consumer_cpu_seconds": consumer_cpu,
        "consumer_rows": consumer,
        "pair": pair,
        "solver": solver,
        **metrics,
    }
    if metrics.get("logical_input_bytes", 0):
        row["encoded_write_ratio"] = metrics["encoded_write_bytes"] / metrics["logical_input_bytes"]
        row["chunk_payload_ratio"] = metrics["chunk_payload_bytes"] / metrics["logical_input_bytes"]
    else:
        row["encoded_write_ratio"] = None
        row["chunk_payload_ratio"] = None
    return row, model, frontier


def run_scaling() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    cases: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    max_events = 0
    solver_queries = 0
    config = {
        "sizes": [32, 128, 512],
        "families": ["shared", "unique", "churn"],
        "seeds": [20000, 20001, 20002, 20003, 20004],
        "names": 8,
        "chunks_per_put": 4,
        "chunk_bytes": 256,
        "delete_probability_for_churn": "1/4",
        "policies": {
            "no-dedup": {"deduplicate": False, "batch": 4},
            "sync-metadata": {"deduplicate": True, "batch": 1},
            "unchecked-dedup": {"deduplicate": True, "batch": 4},
            "certified-dedup": {"same_trace_as": "unchecked-dedup", "offline_consumer": True},
        },
    }
    save_json(str(INPUTS / "scaling" / "campaign.json"), config)

    for size in config["sizes"]:
        for family in config["families"]:
            for seed in config["seeds"]:
                operations = scaling_operations(size, family, seed, chunk_size=256)
                traces = {
                    "no-dedup": build_trace(operations, deduplicate=False, batch_size=4),
                    "sync-metadata": build_trace(operations, deduplicate=True, batch_size=1),
                    "unchecked-dedup": build_trace(operations, deduplicate=True, batch_size=4),
                }
                models: dict[str, Any] = {}
                rows: dict[str, dict[str, Any]] = {}
                for policy, trace in traces.items():
                    pair_solver = policy == "unchecked-dedup" and size <= 128
                    row, model, frontier = _analyze_policy(trace, label=policy, pair_solver=pair_solver)
                    row.update({"size": size, "family": family, "seed": seed})
                    rows[policy] = row
                    models[policy] = model
                    max_events = max(max_events, model.n)
                    if pair_solver and USE_SMT:
                        solver_queries += 1
                certified = dict(rows["unchecked-dedup"])
                certified["policy"] = "certified-dedup"
                certified["offline_certificate_consumed"] = True
                rows["certified-dedup"] = certified
                cases.extend(rows[p] for p in ["no-dedup", "sync-metadata", "unchecked-dedup", "certified-dedup"])

                for policy in ["no-dedup", "sync-metadata", "unchecked-dedup"]:
                    model = models[policy]
                    full = list(range(model.n))
                    rec = _timed_recovery(model, full)
                    rec.update({"size": size, "family": family, "seed": seed, "policy": policy, "sample": "full"})
                    recoveries.append(rec)
                    if seed == 20000:
                        for sample_index, cut in enumerate(_sample_cuts(model, 16, seed + size + len(family) + len(policy))):
                            rec = _timed_recovery(model, cut)
                            rec.update({
                                "size": size,
                                "family": family,
                                "seed": seed,
                                "policy": policy,
                                "sample": f"cut-{sample_index:02d}",
                            })
                            recoveries.append(rec)
    _jsonl(RESULTS / "scaling" / "policy-metrics.jsonl", cases)
    _jsonl(RESULTS / "scaling" / "recovery.jsonl", recoveries)
    result = {
        "stage": "scaling",
        "instances": 45,
        "policy_rows": len(cases),
        "recovery_images": len(recoveries),
        "recovery_validation_checks": {
            "json_roundtrip": len(recoveries),
            "idempotence": len(recoveries),
            "reference_multiplicity": len(recoveries),
            "quiescent_reclamation": len(recoveries),
        },
        "solver_queries": solver_queries,
        "max_events": max_events,
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "scaling" / "summary.json"), result)
    return result


def _sqlite_files(version: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name in ["hash.h", "mutex.h", "pcache.h"]:
        result[name] = (INPUTS / "sqlite" / f"{version}-{name}").read_bytes()
    return result


def run_public() -> dict[str, Any]:
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    old = _sqlite_files("version-3.46.0")
    new = _sqlite_files("version-3.47.0")
    if sum(map(len, old.values())) + sum(map(len, new.values())) != 25582:
        raise AssertionError("public replay bytes differ from the frozen input total")
    unchanged = all(old[name] == new[name] for name in old)
    rows: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    solver_queries = 0
    for chunk_size in [256, 1024, 4096]:
        operations = public_replay_operations(old, new, chunk_size)
        if len(operations) != 18:
            raise AssertionError("public replay operation count changed")
        traces = {
            "no-dedup": build_trace(operations, deduplicate=False, batch_size=4),
            "sync-metadata": build_trace(operations, deduplicate=True, batch_size=1),
            "unchecked-dedup": build_trace(operations, deduplicate=True, batch_size=4),
        }
        for policy, trace in traces.items():
            row, model, _ = _analyze_policy(trace, label=policy, pair_solver=(policy == "unchecked-dedup"))
            row.update({"chunk_size": chunk_size, "operations": len(operations)})
            rows.append(row)
            if policy == "unchecked-dedup" and USE_SMT:
                solver_queries += 1
            full = _timed_recovery(model, list(range(model.n)))
            full.update({"chunk_size": chunk_size, "policy": policy, "sample": "full"})
            recoveries.append(full)
            for sample_index, cut in enumerate(_sample_cuts(model, 16, 30000 + chunk_size + len(policy))):
                rec = _timed_recovery(model, cut)
                rec.update({"chunk_size": chunk_size, "policy": policy, "sample": f"cut-{sample_index:02d}"})
                recoveries.append(rec)
    _jsonl(RESULTS / "public" / "policy-metrics.jsonl", rows)
    _jsonl(RESULTS / "public" / "recovery.jsonl", recoveries)
    result = {
        "stage": "public",
        "input_bytes": 25582,
        "unchanged_across_selected_tags": unchanged,
        "chunk_sizes": 3,
        "policy_rows": len(rows),
        "recovery_images": len(recoveries),
        "recovery_validation_checks": {
            "json_roundtrip": len(recoveries),
            "idempotence": len(recoveries),
            "reference_multiplicity": len(recoveries),
            "quiescent_reclamation": len(recoveries),
        },
        "solver_queries": solver_queries,
        **_resource_record(start_wall, start_cpu),
    }
    save_json(str(RESULTS / "public" / "summary.json"), result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=["tests", "pilots", "census", "heldout", "fallback", "scaling", "public", "all"],
        default="all",
    )
    args = parser.parse_args(argv)
    _mkdirs()
    if args.stage == "all":
        # Replace this import-heavy process with a stdlib-only orchestrator.
        # Each scientific stage then starts in a clean bounded interpreter.
        os.execv(
            sys.executable,
            [sys.executable, str(REPO / "scripts" / "run_all.py")],
        )
        raise AssertionError("os.execv unexpectedly returned")

    _limits()
    functions = {
            "tests": run_tests,
            "pilots": run_pilots,
            "census": run_census,
            "heldout": run_heldout,
            "fallback": run_fallback,
            "scaling": run_scaling,
            "public": run_public,
    }
    campaign_start_wall, campaign_start_cpu = time.perf_counter(), time.process_time()
    print(f"[reproduce] {args.stage}", flush=True)
    completed = [functions[args.stage]()]
    record = {
        "requested_stage": args.stage,
        "execution_mode": "single-stage",
        "completed": completed,
        **_resource_record(campaign_start_wall, campaign_start_cpu),
    }
    save_json(str(RESULTS / "campaign-run.json"), record)
    print(json.dumps(record, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
