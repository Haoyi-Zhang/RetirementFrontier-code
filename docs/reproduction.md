# Reproduction and observation provenance

The supplied primary census, variant, fallback, scaling and public-byte records
are retained. Their byte costs, identifiers, decisions, minima, recovery counts,
SMT counts and host measurements are not relabeled as fresh measurements. The
original campaign record is `results/measurement-campaign.json`. Its old test
count describes that measurement run, not the current suite.

A fresh seven-stage full-mode run of the repaired code completed successfully
in a separately copied clean tree. Its actual record is
`results/campaign-run.json`; its 75-method unit-test result replaces the affected
`results/pilots/unit-tests.json`. Reaggregation updates the test count in the
summary without replacing the retained measurement families. This mixed origin
is intentional and explicit. The repaired parsers perform additional complete-
trace work, so inherited timing samples must not be claimed as fresh performance
measurements of the repaired implementation.

Comparison of all 17 primary result files found identical logical content after
normalizing only the validator's listed host fields and unittest elapsed time.
The input, source, script and test directories matched byte for byte. The fresh
POSIX illustration still reports 11 observations, zero ordered failures and two
unsafe failures. Counts in the manuscript's Tables 3–7 are unchanged; the cost
formula is clarified rather than changed. Exact comparison details are retained
in `reproduction-check.json`. These are same-project executable checks.

Run from the repository root:

```sh
python scripts/check_targeted.py
python scripts/run_tests.py -v
python scripts/reproduce_portable.py --require-smt
python scripts/aggregate.py
python scripts/run_posix_smoke.py
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
