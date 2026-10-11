# Retirement Frontiers

Executable trace-level crash certificates for deduplicating stores, accompanying the original research article prepared for **ACM Transactions on Storage (TOS)**.

## Scope

The finite model has serial events, atomic byte-bearing DATA records (including whole manifests), FREE records, immutable retained ROOTs, unique physical incarnations, and an explicit backward dependency DAG. A crash cut is downward closed. Strict recovery selects the greatest durable ROOT. Acknowledgements promise a recoverable operation prefix, not indefinite retention of overwritten object versions.

The mathematical results characterize this model, not arbitrary programs or hardware. The byte-bearing model, exact oracle, separate certificate consumer, optional SMT encoding, and POSIX illustration have distinct trust boundaries. The POSIX example uses a current-root selector and whole-file replacement helpers. An exact completed-helper projection maps prepared root files to manifests and selector updates to ROOT publications; it is not an all-state filesystem refinement or a power-loss experiment. See `docs/selector-correspondence.md`.

## Requirements

Core experiments use Python 3.10 or later and the standard library, on a POSIX host with `resource` and process support. The recorded reproduction used Ubuntu 24.04 and Python 3.12. Complete SMT comparisons additionally require a usable local Z3 C shared library, discoverable by `ctypes.util.find_library`, or a local `z3-solver` installation. The campaign uses included inputs; TeX is unnecessary for this standalone repository.

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

This requests every non-SMT stage and records omitted solver queries and the solver-dependent unit-test skip. `--require-smt` fails before the campaign if the actual C API is unavailable. Query-budget exhaustion in the bounded fallback analyzer is `unknown`, not `safe`.

## Certificate interface

Current producer and code-separated consumer independently keep a running
same-slot maximum after collecting last users over the complete trace. Each
writer is checked before its own DATA allocation updates the maximum; FREEs
never reset it. This removes repeated earlier-allocation scans, not publication
searches, byte/digest validation, closure storage or minimum-cut pair enumeration.
It makes no measured end-to-end or device performance claim. Three new portable
methods are included by the existing `scripts/run_tests.py` discovery and CI:

```sh
python -B -m unittest discover -s tests -p test_slot_frontier.py -v
```

This command needs only current included sources/fixtures and the standard
library. Slot checks compare a dense pair
definition, the unchanged pair baseline and bounded concrete all-cut recovery.

```sh
python src/cli.py certify inputs/pilot-trace.json --minimum --certificate certificate.json
python src/cli.py verify inputs/pilot-trace.json certificate.json
```

The certificate can be removed after inspection. The consumer reparses the original trace and validates certificate coverage and witnesses. It does not import the producer, engine, oracle or solver. It was developed within the same project and is not an independent-team implementation or a machine proof. `docs/formats.md` specifies canonical maximum-root witnesses, controlled CLI statuses, all wire formats, and the separate descriptor-plus-raw-payload cost model; strict JSON rejects duplicate keys and nonfinite numbers.

## What to check

The supplied full result contains 33,792 dependency graphs and 2,129,920 candidate event sets; 3,072 graphs are safe and 30,720 unsafe graphs have oracle-checked minimum witnesses. The regression set contains 256 variants (75 safe, 181 unsafe). Fallback tests cover 255 formulas and 2,040 assignments plus 92 fixed-candidate formulas. Scaling has 45 base inputs and 180 policy report rows, with at most 5,599 events. All 720 recovery images pass selected-root, prefix, and specification-byte comparisons and account for 5,382 unreachable occupied-slot observations. The campaign executes 65 evaluation SMT queries plus two pilot queries, with zero unknowns, and all 103 unit methods pass with no skips. The POSIX fixture has eleven agreeing selector projections, zero ordered failures, and two expected unsafe failures.

`check_scientific_invariants.py` checks exact JSON paths and cross-checks primary row counts, decisions, minimum-witness flags, certificate acceptance and byte arithmetic. It does not accept a matching number found elsewhere in a JSON tree. Assertions are ordinary executable checks, not proof-assistant verification.

A separate local Windows API check re-enumerates the census, retained variants,
fallback assignments, and 92 fixed-candidate minima without a disagreement. It
regenerates 144 physical policy traces and checks 720 recovered images against
the selected root's specification bytes, multiplicities, repeated recovery, and
quiescent reclamation. The specification-byte comparison is additional to the
historical timed recovery path. No SMT query or POSIX fixture runs in this check;
these supplementary observations are separate from the Ubuntu primary campaign.

The test count must agree between the primary runner output and its summary,
with the retained 75-method suite as a lower bound. New regressions need not
masquerade as the historical suite. `run_all.py` shares one 45-minute wall-time
deadline across all seven stages. The prepared scientific-checks workflow runs
from this flat artifact root on Ubuntu 24.04 with a 40-minute scientific-command
deadline, a 45-minute job cap, resource limits, mandatory SMT preflight, and raw
output uploads even after failure.

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

Small-domain recovery compares bytes with the offline oracle. The 720-image
campaign also compares every selected-root namespace with its specification,
alongside multiplicity, repeated recovery, and reclamation checks.

## Full-history recovery and publication-valid fallback

```sh
python -B scripts/run_tests.py --pattern test_recovery_subclasses.py -v
python -B scripts/check_recovery_subclasses.py
python -B src/cli.py fallback-publication inputs/pilot-trace.json
```

The publication-valid checker first checks P, then uses a deletion worklist to
decide universal acknowledgement safety for an unbounded candidate set. Failure
of P returns `not-applicable`, safe=null, exit code 2; general fallback still uses
the separate fixed-candidate checker. The polynomial checker returns a greatest
violating cut, never a minimum-cardinality claim. Its proof is in
`docs/theorems.md` and its graph/byte-oracle rows are in
`results/recovery-subclasses/publication-fallback.jsonl.gz`.

The recovery sampler now splits its nonempty budget between evenly spaced root
and event issue ranks over the complete history. It excludes the separately
measured full cut and fills duplicate closures explicitly. New rows record exact
cut event identifiers, selected prefix, and maximum event. `reproduce.py` also
compares the selected root, prefix and recovered bytes with the specification.
The prior 16-cut sampler exhausted its budget at the empty cut and first fifteen
roots, reaching prefix 60 at batch four or 15 at batch one in a 512-operation
history. The supplied 720-image campaign uses stratified cuts and records their
current recovery timings and occupancy totals.

The separate local check examines 512-operation histories for all three workload
families, five seeds and three physical policies. It stores real local image-file
roundtrips and abstract recovery checks in `results/recovery-subclasses/`, not new
POSIX persistence observations. The directed regression erases a selected
manifest only at prefixes >=400 to test sampling sensitivity; this is an injected
image fault, not a counterexample to the unmodified safe generated trace.

The executed local check examines 33,792 graphs, retaining 6,528 satisfying P,
and materializes 64,458 legal fallback images. Its 19,200 acknowledgement checks
(9,984 unsafe) match byte enumeration and the union defining the greatest fault
cut. The 45 full-length histories pass all original acknowledgement checks and
produce 765 verified local image-file roundtrips. All four prefix quarters are
represented, and each proper-cut set reaches prefix 512. The directed late fault
is missed by old cuts and detected in four stratified cuts. Ten focused unit
methods pass, with no skips. `summary.json`, primary JSONL rows and
`unit-tests.json` retain these observations separately from the seven-stage primary campaign.

## Directed contract checks

```sh
python scripts/check_targeted.py
```

The explicit fixtures in `tests/targeted_cases.py` bypass the workload generator.
They cover forward manifest/chunk bindings, truly missing allocations, multiple
acknowledgements and budgets, canonical witnesses with several qualifying roots,
and non-string role/kind inputs through the CLI. See `docs/contract-validation.md`
for the contract definitions and measurement provenance.

## License

Original project code is available under the MIT License. Notices for third-party
inputs and dependencies are retained in `PROVENANCE.md` and `licenses/`.
