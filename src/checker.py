"""Frontier checker, pair baseline, certificates, and minimum witnesses."""
from __future__ import annotations

import json
import time
from typing import Any

from model import Model, admit_trace, ref_key


def _root_prefix(model: Model, root_id: int) -> int:
    return int(model.events[root_id]["prefix"])


def analyze_model(model: Model, minimum: bool = False) -> dict[str, Any]:
    start = time.process_time()
    publication_rows: list[dict[str, int]] = []
    publication_failures: list[dict[str, Any]] = []
    last_user: dict[int, int] = {i: -1 for i, e in enumerate(model.events) if e["kind"] == "data"}

    for root_id in model.roots:
        for ref_index, ref in enumerate(model.refs_by_root[root_id]):
            alloc = model.data_by_key.get(ref_key(ref))
            if alloc is None or not (model.closures[root_id] & (1 << alloc)):
                publication_failures.append({"root": root_id, "ref_index": ref_index, "allocation": alloc})
            else:
                publication_rows.append({"root": root_id, "ref_index": ref_index, "allocation": alloc})
            if alloc is not None:
                last_user[alloc] = max(last_user.get(alloc, -1), root_id)

    retirement_rows: list[dict[str, int]] = []
    retirement_failures: list[dict[str, Any]] = []
    frontier_obligations = 0
    for slot, writers in sorted(model.writers_by_slot.items()):
        frontier = -1
        for writer in writers:
            event = model.events[writer]
            if frontier >= 0:
                frontier_obligations += 1
                forced_roots = [r for r in model.roots if model.closures[writer] & (1 << r)]
                guard = max(forced_roots, default=-1)
                if guard <= frontier:
                    retirement_failures.append({"writer": writer, "frontier": frontier, "guard": guard})
                else:
                    retirement_rows.append({"writer": writer, "guard_root": guard})
            if event["kind"] == "data":
                frontier = max(frontier, last_user.get(writer, -1))

    acknowledgement_rows: list[dict[str, int]] = []
    acknowledgement_failures: list[dict[str, Any]] = []
    for ack_index, ack in enumerate(model.acknowledgements):
        p = int(ack["prefix"])
        if p == 0:
            acknowledgement_rows.append({"ack_index": ack_index, "root": -1})
            continue
        closure_bits = 0
        for anchor in ack["anchors"]:
            closure_bits |= model.closures[anchor]
        candidates = [r for r in model.roots if (closure_bits & (1 << r)) and _root_prefix(model, r) >= p]
        if not candidates:
            acknowledgement_failures.append({"ack_index": ack_index, "prefix": p})
        else:
            acknowledgement_rows.append({"ack_index": ack_index, "root": max(candidates)})

    safe = not publication_failures and not retirement_failures and not acknowledgement_failures
    result: dict[str, Any] = {
        "safe": safe,
        "publication_failures": publication_failures,
        "retirement_failures": retirement_failures,
        "acknowledgement_failures": acknowledgement_failures,
        "frontier_obligations": frontier_obligations,
        "certificate": None,
        "minimum_bad_cut": None,
        "producer_cpu_seconds": time.process_time() - start,
    }
    if safe:
        certificate = {
            "schema": "retirement-frontier-certificate-v1",
            "publication": publication_rows,
            "retirement": retirement_rows,
            "acknowledgement": acknowledgement_rows,
        }
        result["certificate"] = certificate
        result["certificate_bytes"] = len(json.dumps(certificate, sort_keys=True, separators=(",", ":")).encode())
    else:
        result["certificate_bytes"] = 0
        if minimum:
            candidates: list[set[int]] = []
            for failure in publication_failures:
                candidates.append(model.closure_ids(failure["root"]))
            # Enumerate every threatened root/writer pair, not only the scalar frontier.
            for root_id in model.roots:
                for ref in model.refs_by_root[root_id]:
                    alloc = model.data_by_key.get(ref_key(ref))
                    if alloc is None or not (model.closures[root_id] & (1 << alloc)):
                        continue
                    slot = int(ref["slot"])
                    for writer in model.writers_by_slot.get(slot, ()):
                        if writer <= alloc:
                            continue
                        forced_roots = [r for r in model.roots if model.closures[writer] & (1 << r)]
                        guard = max(forced_roots, default=-1)
                        if guard <= root_id:
                            candidates.append(model.closure_ids([root_id, writer]))
            for failure in acknowledgement_failures:
                ack = model.acknowledgements[failure["ack_index"]]
                candidates.append(model.closure_ids(ack["anchors"]))
            if candidates:
                result["minimum_bad_cut"] = sorted(min(candidates, key=lambda x: (len(x), tuple(sorted(x)))))
    return result


def analyze_trace(trace: dict[str, Any], minimum: bool = False) -> dict[str, Any]:
    return analyze_model(admit_trace(trace), minimum=minimum)


def pair_analyze_model(model: Model, minimum: bool = False) -> dict[str, Any]:
    start = time.process_time()
    failures: list[dict[str, Any]] = []
    candidates: list[set[int]] = []
    pair_obligations = 0

    # Publication is checked directly.
    for root_id in model.roots:
        for ref_index, ref in enumerate(model.refs_by_root[root_id]):
            alloc = model.data_by_key.get(ref_key(ref))
            if alloc is None or not (model.closures[root_id] & (1 << alloc)):
                failures.append({"kind": "publication", "root": root_id, "ref_index": ref_index})
                candidates.append(model.closure_ids(root_id))
                continue
            for writer in model.writers_by_slot.get(int(ref["slot"]), ()):
                if writer <= alloc:
                    continue
                pair_obligations += 1
                later_forced = [r for r in model.roots if model.closures[writer] & (1 << r)]
                if max(later_forced, default=-1) <= root_id:
                    failures.append({"kind": "interference", "root": root_id, "allocation": alloc, "writer": writer})
                    candidates.append(model.closure_ids([root_id, writer]))

    for ack_index, ack in enumerate(model.acknowledgements):
        p = int(ack["prefix"])
        if p == 0:
            continue
        bits = 0
        for anchor in ack["anchors"]:
            bits |= model.closures[anchor]
        if not any((bits & (1 << r)) and _root_prefix(model, r) >= p for r in model.roots):
            failures.append({"kind": "acknowledgement", "ack_index": ack_index})
            candidates.append(model.closure_ids(ack["anchors"]))

    result = {
        "safe": not failures,
        "failures": failures,
        "pair_obligations": pair_obligations,
        "minimum_bad_cut": None,
        "pair_cpu_seconds": time.process_time() - start,
    }
    if minimum and candidates:
        result["minimum_bad_cut"] = sorted(min(candidates, key=lambda x: (len(x), tuple(sorted(x)))))
    return result
