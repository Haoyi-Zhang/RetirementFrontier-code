# Retirement Frontiers

Executable trace-level crash certificates for deduplicating stores, accompanying the original research article prepared for **ACM Transactions on Storage (TOS)**.

## Scope

The finite model has serial events, atomic byte-bearing DATA records (including whole manifests), FREE records, immutable retained ROOTs, unique physical incarnations, and an explicit backward dependency DAG. A crash cut is downward closed. Strict recovery selects the greatest durable ROOT. Acknowledgements promise a recoverable operation prefix, not indefinite retention of overwritten object versions.

The mathematical results characterize this model, not arbitrary programs or hardware. The byte-bearing model, exact oracle, separate certificate consumer, optional SMT encoding, and POSIX illustration have distinct trust boundaries. The POSIX example uses a current-root selector and whole-file replacement helpers; it does not refine the greatest-root theorem and is not a power-loss experiment.

## Requirements

Core experiments use Python 3.10 or later and the standard library, on a POSIX host with `resource` and process support. The current reproduction used Python 3.13. Complete SMT comparisons additionally require a usable local Z3 C shared library, discoverable by `ctypes.util.find_library`, or a local `z3-solver` installation. No live network, model service, GPU, database service or private dataset is used by the campaign. TeX is unnecessary for this standalone repository.

## Reproduce the evidence

From this directory:

```sh
python scripts/run_tests.py -v
python scripts/reproduce_portable.py --require-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/validate_release.py
```

The full campaign runs seven sequential bounded stages: tests, pilots, census, retained variants, fallback, scaling, and public bytes. The POSIX illustration is an additional explicit command. The campaign records per-stage CPU, wall time and process peak RSS. The single-worker default is intentional; do not replace it with unbounded parallel runs.

For a standard-library-only run, in a separate extraction so that the full results are not overwritten:

```sh
python scripts/reproduce_portable.py --without-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/validate_release.py
```

This runs every non-SMT stage (74 test methods pass and one solver-dependent method is explicitly skipped), records omitted SMT queries, and never calls an omission a successful solver check. `--require-smt` fails before the campaign if the actual C API is unavailable. Query-budget exhaustion in the bounded fallback analyzer is `unknown`, not `safe`.

## Certificate interface

```sh
python src/cli.py certify inputs/pilot-trace.json --minimum --certificate certificate.json
python src/cli.py verify inputs/pilot-trace.json certificate.json
```

The certificate can be removed after inspection. The consumer reparses the original trace and validates certificate coverage and witnesses. It does not import the producer, engine, oracle or solver. It was developed within the same project and is not an independent-team implementation or a machine proof. `docs/formats.md` specifies canonical maximum-root witnesses, controlled CLI statuses, all wire formats, and the separate descriptor-plus-raw-payload cost model; strict JSON rejects duplicate keys and nonfinite numbers.

## What to check

The canonical full result contains 33,792 dependency graphs and 2,129,920 candidate event sets; 3,072 graphs are safe and 30,720 unsafe graphs have oracle-checked minimum witnesses. The retained regression set contains 256 variants (75 safe, 181 unsafe). Fallback tests cover 255 formulas and 2,040 assignments plus 92 fixed-candidate formulas. Scaling has 45 base inputs and 180 policy report rows, with at most 5,599 events. There are 720 byte-recovery images and 3,765 unreachable occupied-slot observations. The full run has 65 evaluation SMT queries plus two pilot queries, 67 total, with zero unknowns. The current suite has 75 methods.

`check_scientific_invariants.py` checks exact JSON paths and cross-checks primary row counts, decisions, minimum-witness flags, certificate acceptance and byte arithmetic. It does not accept a matching number found elsewhere in a JSON tree. Assertions are ordinary executable checks, not proof-assistant verification.

To compare a same-mode clean rerun against another extracted artifact directory:

```sh
python scripts/validate_release.py --compare /path/to/other/artifact
```

All 17 primary result files are structurally compared; only an explicit list of host timing and resource fields and the unittest elapsed-time text are normalized. Input, source, script and test files are compared byte-for-byte without retaining a release hash manifest. Counts, decisions, witness identities, byte costs and solver statuses are not normalized. Full and core-only runs are intentionally not declared interchangeable.

## Interpretation and limitations

The input directory named `heldout` is retained for interface stability. Its cases have been revisited during repairs and are **regression inputs, not an untouched test set**. The algorithms are not trained statistical models. Generated sample counts do not establish production-workload breadth. Six SQLite header files are three identical pairs across the selected tags; they are exact public bytes, not filesystem access traces.

Certified and unchecked batch-four deduplication are two reports of the same storage trace, not two independent storage engines. The certificate adds offline analysis, not extra durable writes. Chunk savings must be read together with encoded metadata costs. Python validation timings are descriptive host measurements, not device performance or speedup confidence intervals.

No concurrent publication/cleaning, torn writes, distributed faults, generation wraparound, root-history truncation, indefinite snapshot retention, or crash-during-mutating-recovery claim is made. SHA-256 is the implementation identifier; the mathematical model uses exact content identity. See `docs/theorems.md` and `docs/trust-boundaries.md`.

## Contents and provenance

`src/`: model, analyzer, separate consumer, recovery, oracle, solver, engine and POSIX illustration. `scripts/`: bounded experiments and checks. `tests/`: regressions. `inputs/`: exact public bytes and deterministic configurations/cases. `results/`: primary outputs and derived summaries. `docs/`: model/format/claim documentation. `claim_evidence_ledger.csv`: scientific claim-to-evidence map. `external_resources.csv`, `PROVENANCE.md`, and `licenses/`: source attribution.

Generative AI assisted research formulation, proof drafting, programming, debugging, experiment orchestration, analysis, figures and writing. Computational observations come from the executed supplied programs. Human authors must substantively verify, contribute, approve, resolve rights and accept responsibility before external submission. No external publication or independent peer review is implied by the package.

## Directed contract checks

```sh
python scripts/check_targeted.py
```

The explicit fixtures in `tests/targeted_cases.py` bypass the workload generator.
They cover forward manifest/chunk bindings, truly missing allocations, multiple
acknowledgements and budgets, canonical witnesses with several qualifying roots,
and non-string role/kind inputs through the CLI. Pre-edit observations actually
executed on the supplied packet are retained in
`results/targeted/binding-minimum-format-before.json`; current observations are
written to `binding-minimum-format-after.json`. The former is counterexample
evidence, not a current safety result. See `docs/contract-validation.md` for the
meaning and measurement provenance of both.
