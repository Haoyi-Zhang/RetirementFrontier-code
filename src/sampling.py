"""Deterministic full-history legal-cut selection for recovery experiments."""
from __future__ import annotations

import random
from typing import Any

from model import Model


def _spread(values: list[int], count: int) -> list[int]:
    """Include both endpoints and evenly spaced issue ranks (no random claim)."""
    if not values or count <= 0:
        return []
    count = min(count, len(values))
    if count == 1:
        return [values[-1]]
    return [values[i * (len(values) - 1) // (count - 1)] for i in range(count)]


def sample_cuts(model: Model, count: int, seed: int) -> list[list[int]]:
    """Select proper legal cuts, stratifying roots AND writers over all history.

    The separately measured full cut is excluded. Empty is a control; the
    remaining budget is split between root and event issue-rank strata. Duplicate
    closures are filled from all root/event closures before bounded random
    unions. This is deterministic coverage, not a representative distribution.
    """
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    if count == 0:
        return []
    seen: set[tuple[int, ...]] = set()
    cuts: list[list[int]] = []

    def add(seeds: list[int]) -> None:
        cut = tuple(sorted(model.closure_ids(seeds)))
        if len(cut) == model.n or cut in seen or len(cuts) == count:
            return
        seen.add(cut)
        cuts.append(list(cut))

    add([])
    root_budget = count // 2
    event_budget = count - 1 - root_budget
    for root in _spread(list(model.roots), root_budget):
        add([root])
    for event in _spread(list(range(model.n)), event_budget):
        add([event])
    for event in list(reversed(model.roots)) + list(range(model.n - 1, -1, -1)):
        if len(cuts) == count:
            break
        add([event])
    rng = random.Random(seed)
    for _ in range(count * 20):
        if len(cuts) == count or model.n == 0:
            break
        width = 1 + rng.randrange(min(8, model.n))
        add([rng.randrange(model.n) for _ in range(width)])
    if len(cuts) != count:
        raise RuntimeError(f"could not construct {count} distinct proper legal cuts")
    return cuts


def cut_metadata(model: Model, cut: list[int]) -> dict[str, Any]:
    present = set(cut)
    roots = [r for r in model.roots if r in present]
    root = roots[-1] if roots else None
    return {
        "cut_event_ids": sorted(present),
        "max_event_in_cut": max(present, default=-1),
        "selected_root_prefix": 0 if root is None else model.events[root]["prefix"],
        "sampling_method": "root-and-event-issue-rank-strata",
    }
