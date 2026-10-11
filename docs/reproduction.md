# Reproduction and observation provenance

The supplied primary results come from a completed seven-stage run on Ubuntu
24.04 with Python 3.12 and Z3. `results/campaign-run.json` records each executed
stage; `results/pilots/unit-tests.json` records 103 passing methods and no skips.
The census, retained variants, fallback, scaling, public-byte rows, and derived
summary belong to that same run.

Recovery uses deterministic full-history stratification. All 720 primary images
agree on selected root, operation prefix, and specification object bytes, and
contain 5,382 unreachable-slot observations. Seven-call recovery medians have
median 3,965,117 ns, with range 2,925--13,837,182 ns. Scaling peak process RSS
is 162,268 KiB. These are host observations, not device latency measurements.
Specification comparisons occur after the timed calls.

The separate POSIX fixture records eleven completed-helper observations, zero
ordered failures, two expected unsafe failures, and agreement with the abstract
selector projection. Publication-valid fallback and 512-operation local-file
campaigns have their own primary rows under `results/recovery-subclasses/`.

Run from the repository root:

```sh
python scripts/check_targeted.py
python scripts/run_tests.py -v
python scripts/reproduce_portable.py --require-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
python scripts/check_recovery_subclasses.py
python scripts/validate_release.py
```

The complete reproduction deliberately creates fresh host observations. Run it
in a separate extraction to preserve the supplied measurement samples. Use
`--without-smt` in place of `--require-smt` for a core-only campaign; the mode and
omissions are explicit. To compare same-mode extractions, use
`python scripts/validate_release.py --compare /path/to/other/artifact`.

The separately stored directed before-edit observations are necessary
counterexample evidence, not previous reviewer opinions or current expected
results. `scripts/check_targeted.py` regenerates current observations, and the
regression suite checks the repaired contracts.
