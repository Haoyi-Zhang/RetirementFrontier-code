"""Optional exact strict-safety query through the local Z3 C library."""
from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import os
from pathlib import Path
import time
from typing import Any

from model import Model, admit_trace, ref_key


class SolverUnavailable(RuntimeError):
    pass


def _library_candidates() -> list[str]:
    """Resolve the system C library or a library bundled with z3-solver."""
    paths: list[str] = []
    system = ctypes.util.find_library("z3")
    if system:
        paths.append(system)
    spec = importlib.util.find_spec("z3")
    if spec and spec.submodule_search_locations:
        for directory in spec.submodule_search_locations:
            for pattern in ("lib/libz3.so*", "lib/libz3.dylib", "lib/libz3.dll", "libz3.dll"):
                paths.extend(str(p) for p in sorted(Path(directory).glob(pattern)))
    return list(dict.fromkeys(paths))


def _load_library() -> ctypes.CDLL:
    if os.environ.get("RF_SKIP_SMT") == "1":
        raise SolverUnavailable("SMT explicitly disabled (RF_SKIP_SMT=1)")
    errors = []
    for path in _library_candidates():
        try:
            lib = ctypes.CDLL(path)
            for symbol in ("Z3_mk_config", "Z3_mk_context_rc", "Z3_eval_smtlib2_string",
                           "Z3_del_context", "Z3_del_config"):
                getattr(lib, symbol)
            return lib
        except (OSError, AttributeError) as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) or "no library candidates"
    raise SolverUnavailable("usable Z3 C library not found: " + detail)


def solver_available() -> bool:
    """Probe the actual C API, not the presence of an unrelated Python import."""
    try:
        return _z3_eval("(set-logic QF_UF)\n(check-sat)\n") == "sat"
    except SolverUnavailable:
        return False


def _z3_eval(script: str) -> str:
    lib = _load_library()
    lib.Z3_mk_config.restype = ctypes.c_void_p
    lib.Z3_mk_context_rc.argtypes = [ctypes.c_void_p]
    lib.Z3_mk_context_rc.restype = ctypes.c_void_p
    lib.Z3_eval_smtlib2_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.Z3_eval_smtlib2_string.restype = ctypes.c_char_p
    lib.Z3_del_context.argtypes = [ctypes.c_void_p]
    lib.Z3_del_config.argtypes = [ctypes.c_void_p]
    cfg = lib.Z3_mk_config()
    if not cfg:
        raise SolverUnavailable("Z3_mk_config failed")
    ctx = lib.Z3_mk_context_rc(cfg)
    if not ctx:
        lib.Z3_del_config(cfg)
        raise SolverUnavailable("Z3_mk_context_rc failed")
    try:
        raw = lib.Z3_eval_smtlib2_string(ctx, script.encode("utf-8"))
        if not raw:
            raise SolverUnavailable("Z3 returned no result")
        return raw.decode("utf-8", errors="replace").strip()
    finally:
        lib.Z3_del_context(ctx)
        lib.Z3_del_config(cfg)


def _or(parts: list[str]) -> str:
    if not parts:
        return "false"
    if len(parts) == 1:
        return parts[0]
    return "(or " + " ".join(parts) + ")"


def _and(parts: list[str]) -> str:
    if not parts:
        return "true"
    if len(parts) == 1:
        return parts[0]
    return "(and " + " ".join(parts) + ")"


def solve_model(model: Model) -> dict[str, Any]:
    start = time.process_time()
    lines = ["(set-logic QF_UF)"]
    for i in range(model.n):
        lines.append(f"(declare-fun e{i} () Bool)")
    for i, parents in enumerate(model.deps):
        for parent in parents:
            lines.append(f"(assert (=> e{i} e{parent}))")

    bad_terms: list[str] = []
    roots = list(model.roots)
    for pos, root_id in enumerate(roots):
        selected = _and([f"e{root_id}"] + [f"(not e{later})" for later in roots[pos + 1 :]])
        ref_bad: list[str] = []
        for ref in model.refs_by_root[root_id]:
            alloc = model.data_by_key.get(ref_key(ref))
            if alloc is None:
                ref_bad.append("true")
                continue
            faults = [f"(not e{alloc})"]
            faults.extend(f"e{writer}" for writer in model.writers_by_slot.get(int(ref["slot"]), ()) if writer > alloc)
            ref_bad.append(_or(faults))
        if ref_bad:
            bad_terms.append(_and([selected, _or(ref_bad)]))

    for ack in model.acknowledgements:
        prefix = int(ack["prefix"])
        if prefix == 0:
            continue
        anchors = _and([f"e{int(a)}" for a in ack["anchors"]])
        covering = [f"e{r}" for r in roots if int(model.events[r]["prefix"]) >= prefix]
        bad_terms.append(_and([anchors, f"(not {_or(covering)})"]))

    lines.append(f"(assert {_or(bad_terms)})")
    lines.append("(check-sat)")
    answer = _z3_eval("\n".join(lines) + "\n")
    verdicts = [line.strip().lower() for line in answer.splitlines() if line.strip().lower() in {"sat", "unsat", "unknown"}]
    first = verdicts[-1] if verdicts else ""
    if first not in {"sat", "unsat", "unknown"}:
        raise SolverUnavailable(f"unexpected Z3 response: {answer[:200]}")
    return {
        "status": first,
        "safe": True if first == "unsat" else False if first == "sat" else None,
        "events": model.n,
        "formula_bytes": len(("\n".join(lines) + "\n").encode("utf-8")),
        "solver_cpu_seconds": time.process_time() - start,
    }


def solve_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return solve_model(admit_trace(trace))
