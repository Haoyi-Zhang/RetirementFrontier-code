"""Exact bounded analysis for greatest-valid-root fallback recovery."""
from __future__ import annotations

from itertools import product
import time
from typing import Any, Iterable

from medium import materialize, recover
from model import Model, admit_trace, canonical_manifest, make_ref, ref_key


def fixed_candidate_model(model: Model, max_choices: int = 1_000_000) -> dict[str, Any]:
    """Decide acknowledgement safety by enumerating one failure literal per candidate root.

    The algorithm is exact if the finite choice product is exhausted.  On budget
    exhaustion it returns ``unknown`` rather than treating a partial search as safe.
    """
    if type(max_choices) is not int or max_choices < 0:
        raise ValueError("max_choices must be a nonnegative integer")
    start = time.process_time()
    explored = 0
    best: list[int] | None = None
    violating_ack: int | None = None

    for ack_index, ack in enumerate(model.acknowledgements):
        if int(ack["prefix"]) == 0:
            continue
        candidates = [r for r in model.roots if int(model.events[r]["prefix"]) >= int(ack["prefix"])]
        anchors = [int(x) for x in ack["anchors"]]
        if not candidates:
            cut = sorted(model.closure_ids(anchors))
            if best is None or (len(cut), tuple(cut)) < (len(best), tuple(best)):
                best = cut
                violating_ack = ack_index
            continue

        # Each option is (positive events forced durable, negative events forced absent).
        option_sets: list[list[tuple[tuple[int, ...], tuple[int, ...]]]] = []
        for root_id in candidates:
            options: list[tuple[tuple[int, ...], tuple[int, ...]]] = [ ((), (root_id,)) ]
            for ref in model.refs_by_root[root_id]:
                alloc = model.data_by_key.get(ref_key(ref))
                if alloc is None:
                    options.append(((), ()))
                    continue
                options.append(((), (alloc,)))
                for writer in model.writers_by_slot.get(int(ref["slot"]), ()):
                    if writer > alloc:
                        options.append(((writer,), ()))
            # Deduplicate while preserving deterministic order.
            seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
            deduped: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
            for option in options:
                if option not in seen:
                    seen.add(option)
                    deduped.append(option)
            option_sets.append(deduped)

        for selected in product(*option_sets):
            explored += 1
            if explored > max_choices:
                return {
                    "status": "unknown",
                    "safe": None,
                    "reason": "choice budget exhausted",
                    "minimum_bad_cut": None,
                    "bad_cut": best,
                    "bad_cut_ack_index": violating_ack,
                    "choices_explored": explored - 1,
                    "cpu_seconds": time.process_time() - start,
                }
            positive = set(anchors)
            negative: set[int] = set()
            for pos, neg in selected:
                positive.update(pos)
                negative.update(neg)
            cut_set = model.closure_ids(positive)
            if cut_set & negative:
                continue
            cut = sorted(cut_set)
            outcome = recover(materialize(model, cut), fallback=True)
            if outcome.get("ok") and int(outcome.get("prefix", 0)) < int(ack["prefix"]):
                if best is None or (len(cut), tuple(cut)) < (len(best), tuple(best)):
                    best = cut
                    violating_ack = ack_index

    if best is not None:
        return {
            "status": "unsafe",
            "safe": False,
            "ack_index": violating_ack,
            "minimum_bad_cut": best,
            "choices_explored": explored,
            "cpu_seconds": time.process_time() - start,
        }
    return {
        "status": "safe",
        "safe": True,
        "minimum_bad_cut": None,
        "choices_explored": explored,
        "cpu_seconds": time.process_time() - start,
    }


def fixed_candidate_trace(trace: dict[str, Any], max_choices: int = 1_000_000) -> dict[str, Any]:
    return fixed_candidate_model(admit_trace(trace), max_choices=max_choices)


def _data_event(event_id: int, slot: int, generation: int, data: bytes, deps: Iterable[int]) -> dict[str, Any]:
    ref = make_ref(slot, generation, data)
    return {
        "id": event_id,
        "kind": "data",
        "slot": slot,
        "generation": generation,
        "digest": ref["digest"],
        "length": ref["length"],
        "data_hex": data.hex(),
        "role": "chunk",
        "deps": sorted(set(int(x) for x in deps)),
    }


def formula_trace(clauses: list[tuple[int, int, int]]) -> dict[str, Any]:
    """Encode a three-variable 3-CNF formula as a fallback-safety trace.

    Literals use +/-1, +/-2, +/-3.  A compatible closed cut chooses each
    variable by including its later ``true`` writer or leaving the earlier
    ``false`` allocation as the last writer.  A clause root is valid exactly
    when that clause is false.  Hence an acknowledgement-violating fallback
    cut exists exactly when the formula is satisfiable.
    """
    if not clauses:
        raise ValueError("formula must contain at least one clause")
    for clause in clauses:
        if len(clause) != 3 or any(abs(int(lit)) not in {1, 2, 3} for lit in clause):
            raise ValueError("clauses must contain three literals over variables 1..3")

    events: list[dict[str, Any]] = []
    false_refs: dict[int, dict[str, Any]] = {}
    true_refs: dict[int, dict[str, Any]] = {}
    false_ids: dict[int, int] = {}
    for var in (1, 2, 3):
        f = f"v{var}=0".encode()
        f_id = len(events)
        events.append(_data_event(f_id, var - 1, 1, f, []))
        false_refs[var] = make_ref(var - 1, 1, f)
        false_ids[var] = f_id
        t = f"v{var}=1".encode()
        t_id = len(events)
        events.append(_data_event(t_id, var - 1, 2, t, [f_id]))
        true_refs[var] = make_ref(var - 1, 2, t)

    operations: list[dict[str, Any]] = []
    root_ids: list[int] = []
    next_slot = 3
    for clause_index, clause in enumerate(clauses, 1):
        refs: list[dict[str, Any]] = []
        chunks: list[bytes] = []
        for literal in clause:
            var = abs(int(literal))
            if literal > 0:
                refs.append(false_refs[var])
                chunks.append(f"v{var}=0".encode())
            else:
                refs.append(true_refs[var])
                chunks.append(f"v{var}=1".encode())
        namespace = {"candidate": refs}
        operations.append({"op": "put", "name": "candidate", "chunks_hex": [chunk.hex() for chunk in chunks]})
        manifest = canonical_manifest(namespace)
        manifest_ref = make_ref(next_slot, 1, manifest)
        manifest_id = len(events)
        events.append({
            "id": manifest_id,
            "kind": "data",
            "slot": next_slot,
            "generation": 1,
            "digest": manifest_ref["digest"],
            "length": manifest_ref["length"],
            "data_hex": manifest.hex(),
            "role": "manifest",
            "deps": [],
        })
        root_id = len(events)
        events.append({
            "id": root_id,
            "kind": "root",
            "prefix": clause_index,
            "manifest_ref": manifest_ref,
            "namespace": namespace,
            "objects_hex": {"candidate": b"".join(chunks).hex()},
            "deps": [manifest_id],
        })
        root_ids.append(root_id)
        next_slot += 1

    anchors = sorted(list(false_ids.values()) + root_ids)
    return {
        "schema": "retirement-frontier-trace-v1",
        "operations": operations,
        "events": events,
        "acknowledgements": [{"issued": len(events), "prefix": 1, "anchors": anchors}],
    }


def assignment_cut(model: Model, assignment: tuple[bool, bool, bool]) -> list[int]:
    anchors = list(model.acknowledgements[0]["anchors"])
    # The first six events are false/true writers in variable order.
    selected_true = [2 * i + 1 for i, value in enumerate(assignment) if value]
    return sorted(model.closure_ids(anchors + selected_true))


def evaluates_formula(clauses: list[tuple[int, int, int]], assignment: tuple[bool, bool, bool]) -> bool:
    for clause in clauses:
        if not any((assignment[abs(lit) - 1] if lit > 0 else not assignment[abs(lit) - 1]) for lit in clause):
            return False
    return True


def sign_pattern_clauses() -> list[tuple[int, int, int]]:
    return [
        tuple(var if (mask & (1 << (var - 1))) else -var for var in (1, 2, 3))
        for mask in range(8)
    ]
