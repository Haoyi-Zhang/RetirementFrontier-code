# Completed-helper selector correspondence

The POSIX fixture recovers through `CURRENT`, not through the greatest prepared
`root-epoch.json` file. Preparing a file and publishing its selection are separate
operations. Mapping a prepared file directly to a ROOT marker would select B at
boundary two while the actual fixture still selects A.

`src/selector_bridge.py` instead constructs admitted traces as follows:

| Completed file operation | Abstract event |
| --- | --- |
| Chunk-file replacement | DATA to that slot, physical generation g mapped to g+1 |
| Root-file preparation | DATA manifest in a distinct retained slot, with canonical decoded namespace |
| CURRENT update | ROOT referring to that prepared manifest |

All abstract events have a dependency on their predecessor. These edges describe
the named completed-helper prefixes, not arbitrary OS writeback or in-helper
crashes. Epochs zero and one correspond to operation prefixes one and two. The
initialized A state occupies events 0,1,2. `boundary_cut` starts after that setup,
adds one event per completed helper, and repeats the complete cut for normal
completion. The projection logically retains selector publications; the fixture
retains only the current pointer. For these fixed monotone selectors, the greatest
logical publication and the physical selector agree. No selector rollback or
root deletion is admitted by this correspondence.

**Lemma.** At each named boundary of the two fixed update protocols, after
completed initialization and with the completed files intact, actual selector
recovery and abstract strict recovery agree on success and successful object
bytes.

**Proof.** Initialization creates the same A allocation, decoded manifest, and
selected publication. A chunk replacement changes the corresponding last-writer
incarnation in both representations. Root preparation adds only a manifest and
does not select it. A CURRENT replacement and the new greatest ROOT select the
same manifest; older logical markers cannot affect this maximum. Ordered's final
old-slot write touches no selected reference. Unsafe instead overwrites A's
required incarnation while A remains selected at boundaries one and two, so both
readers reject the stale generation. Induction over these transitions exhausts
the protocols. JSON wrapper bytes are normalized, not equated to the abstract
manifest byte layout. QED.

The ordered projected trace satisfies P/D/A, whereas unsafe fails D. This maps
the observed state sequence to an exact trace-level theorem instance. It is not
an all-state refinement of the filesystem: helper atomicity, lost completed
files, power loss, selector rollback, concurrent writers, and mutating recovery
are outside the bridge. Neither a fsync mock nor abstract recovery is evidence of
POSIX crash persistence.

`scripts/run_posix_smoke.py` now compares actual child-process results with this
projection at every boundary when executed on a POSIX host. The retained
`results/posix-smoke.json` predates those comparison fields and is not overwritten
by Windows validation. `results/recovery-subclasses/selector-projection.json`
contains abstract outcomes only and explicitly records that POSIX syscalls were
not executed. It cannot substitute for rerunning the actual process fixture.
