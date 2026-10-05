# Model, implementation and experiment boundaries

## What is proved

The written arguments in `theorems.md` assume a finite serial trace, atomic records (including a whole flat manifest), unique physical incarnations, immutable roots, and an exact backward persistence dependency graph. Client operations define legal namespaces independently of certificate conditions. Recovery is independently defined over bytes. Publication, retirement and acknowledgement conditions are then proved equivalent to all-cut strict-recovery safety. The finite census does not establish the general theorem.

Last-user aggregation is a maximum over the totally ordered serial roots. It is not an arbitrary-poset antichain compression theorem. Pairwise interference is aggregated into one threshold per destructive writer. Closures and same-slot scans can still be quadratic. Minimum negative witnesses use all relevant root/writer pairs, not just the maximum root.

The fallback reduction has unbounded candidate roots and explicitly encoded content/identifiers. The concrete implementation admits bounded-width identifiers and SHA-256 references. Fixed candidate count yields a separate exact enumeration algorithm. A finite digest space is not used to justify an unbounded hardness theorem.

## What programs check

The producer and separate certificate consumer enforce strict admission, including role binding, canonical manifest encoding, exact namespace reconstruction and numeric types. The consumer does not import the producer, oracle, engine or SMT implementation; both are nevertheless developed in the same project. The byte oracle and SMT encoding share some definitions or admission routines and are not organization-independent replications.

The release validator checks precise primary rows and summaries. A passing validator demonstrates consistency of retained evidence, not absence of all implementation defects. Untrusted traces may consume substantial memory within configured file/choice bounds; no denial-of-service resilience claim is made.

## What the POSIX illustration does not establish

The file helper completes write, flush, file fsync, rename and directory fsync before any injected termination. Its root is selected by a CURRENT file, not by scanning for the greatest durable root. No crash inside an individual helper, physical power loss, firmware behavior, block tearing or certificate consumption is tested. Eleven observations include two unchanged initial-state controls, two normal completions, and seven actual injected process terminations; the two stale-generation failures occur in the unsafe ordering. This is an illustration of a reuse bug, not a refinement proof or a production storage implementation.

## Empirical scope

Regression inputs are not untouched. Zero disagreement is limited to executed overlapping domains, not all algorithms on every scale. Seven repeat recoveries per image establish deterministic read-only results in this model, not recovery that tolerates repeated crashes during mutation. Unreachable occupied slots can be erased only in the stated quiescent selected-root setting, not while old snapshots or concurrent readers remain pinned.

The public-byte set is narrow and intentionally unchanged across versions. Reported cost ratios are model encoding costs. No device measurements, representative workload study, independent human evaluation, or acceptance estimate is claimed.

## Generator and offline image boundary

`TraceBuilder` updates in-memory specification maps and appends events; it has no
separate online medium or medium-backed read API. The harness admits the emitted
trace and independently materializes chosen cuts, then recovery reads the image.
The small-domain oracle compares recovered bytes with ghost expectations.
The separate 720-image campaign checks recovery success and repeatability,
not a fresh oracle comparison of every recovered namespace.
Whole-trace binding admission checks later allocations as well as earlier ones.
Neither offline reconstruction nor the separate POSIX illustration establishes
an online implementation that is absent from this artifact.

Costs use the modeled compact JSON descriptor plus one separator byte and, for
data, raw payload bytes. They exclude trace `id/deps/data_hex` and ghost fields.
See `formats.md` for the exact per-kind field sets.
