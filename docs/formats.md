# Wire formats and trusted boundaries

## Trace: `retirement-frontier-trace-v1`

A trace is a JSON object with `operations`, issue-ordered `events`, and
`acknowledgements`. Event identifiers are exactly their zero-based positions.
Every dependency names an earlier event.

- A `data` event contains `slot`, positive `generation`, SHA-256 `digest`, byte
  `length`, `data_hex`, `role` (`chunk` or `manifest`), and `deps`.
- A `free` event contains `slot`, descriptive `generation`, and `deps`. It is an
  unconditional tombstone, not a generation-checked conditional deletion.
- A `root` event contains an increasing client `prefix`, a `manifest_ref`, a
  ghost `namespace`, ghost `objects_hex`, and `deps`. The ghost fields are
  admission evidence and are not copied into a crash image.
- An acknowledgement contains an issue boundary `issued`, promised operation
  `prefix`, and earlier durable `anchors`.

A reference has exactly `(slot, generation, digest, length)`. Slot-generation
identities are unique. Admission recomputes digests and lengths, validates the
canonical manifest encoding, checks that complete references reconstruct each
ghost object, and checks that every root equals its client-operation prefix.
A syntactically valid reference with no matching allocation remains an admitted
safety failure; malformed traces are rejected.

## Image: `retirement-frontier-image-v1`

Materialization accepts a dependency-closed event set. It retains only the last
present record for each represented slot and every present immutable root
marker. It does not retain the operation log, ghost namespace, dependency DAG,
or overwritten slot bytes.

Strict recovery selects the greatest present root marker and either reconstructs
it or fails. Fallback recovery scans root markers from greatest to least and
uses the first reconstructible one. A reference read validates generation,
length, digest, and bytes. A successful recovery returns object bytes,
reference counts, reachable slots, unreachable occupied slots, root history,
and recovered prefix.

## Certificate: `retirement-frontier-certificate-v1`

A positive certificate has three complete, deterministically ordered arrays:

- `publication`: one `(root, ref_index, allocation)` row for each distinct
  reference (including the manifest) published by each root;
- `retirement`: one `(writer, guard_root)` row for each destructive writer with
  a nonempty last-user frontier;
- `acknowledgement`: one `(ack_index, root)` row for each acknowledgement, with
  `-1` used only for a zero-prefix promise.

The consumer rejects omitted, reordered, duplicated, or altered rows. The
consumer reparses the trace and intentionally imports none of the producer,
engine, oracle, or solver modules. It shares authorship and the Python runtime;
it is code-separated, not independently developed or mechanically verified.

## Bounds and non-claims

Local JSON inputs are limited to 64 MiB. Identifiers are limited to signed
63-bit nonnegative values in the implementation. The mathematical results use
finite natural identifiers and exact content labels. Whole data and root
records are atomic. Declared dependency-closed sets are the complete crash
model. No device cache, filesystem `fsync`, concurrency, distributed failure,
malicious logger, hash-collision attack, or resumed writes after recovery is
modeled.

The JSON decoder rejects nonfinite extension tokens and exponent overflow
that would otherwise decode to an infinite float. Finite floats retain their
JSON-decoder type and remain inadmissible in integer identifier fields.
Image slot keys are canonical ASCII decimal strings within the identifier
bound; non-ASCII digit forms and overlong keys yield a malformed image result
without entering integer conversion.

## Complete-trace allocation binding

Admission has two phases in each parser. The first normalizes fields, indexes
all unique `(slot,generation,digest,length)` allocations, checks client prefixes
and each root's canonical manifest reference, and records root references. After
all events have been scanned, the second checks every existing binding, including
allocations later than their referring root: manifest role and exact canonical
namespace bytes; chunk role for each object reference; and full concatenation
against `objects_hex` whenever all of that object's references resolve. A missing
reference does not suppress role checks on other present references. A genuinely
absent matching allocation remains admitted for safety analysis. No implicit
allocation-before-root restriction is introduced: a valid forward allocation
fails strict publication but can be safe for an anchored fallback promise.

## Canonical certificate witnesses and order

The retirement `guard_root` is **exactly `F(writer)`**, the greatest root in the
writer's reflexive predecessor closure, with `F(writer) > T(writer)`. For a
positive acknowledgement, `root` is **exactly the greatest root in the closure
of its anchors**, and that root's prefix must meet the promise. Prefix zero
always has the `-1` sentinel, even when its anchors force real roots. These are
format requirements in addition to logical safety inequalities. Substitution
of a smaller, otherwise sufficient root is rejected, not silently accepted.

Publication rows traverse roots by increasing issue ID, then the manifest and
first-seen distinct chunk references, visiting object names in sorted order and
references in their object order. Retirement rows visit numeric slots in sorted
order and each slot's writers in issue order, omitting writers with `T < 0`.
Acknowledgement rows follow the supplied acknowledgement list. The consumer
reconstructs all three arrays and compares exact row values and list positions.

## Modeled encoded-write accounting (not wire JSON length)

Let `J(d)` denote the UTF-8 length of
`json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
The reported write costs are:

| Record | Descriptor fields | Cost |
|---|---|---|
| data | `kind,slot,generation,digest,length,role` | `J(d) + 1 + len(raw_payload)` |
| root | `kind,prefix,manifest_ref` | `J(d) + 1` |
| free | `kind,slot,generation` | `J(d) + 1` |

The extra byte is a modeled separator. Raw payload includes manifest bytes;
it is not doubled into `data_hex`. Event `id`, `deps`, `data_hex`, root ghost
`namespace` and `objects_hex`, acknowledgement records, and trace/image envelopes
are not counted. The generator accumulates this descriptor-plus-payload model;
it does not emit or measure a wire-format I/O stream. Existing numerical cost
tables retain this definition. Their values must not be relabeled as the size
of complete JSON trace events or actual device writes.

## Controlled outcomes and budgets

The CLI emits an explicit JSON `status`. `certify` returns safe/unsafe;
`fallback` returns safe/unsafe/unknown. Successful decisions exit 0, unsafe
decisions exit 1, and malformed input or unknown exits 2. Callers must inspect
`status` to distinguish the latter two. Non-string role/kind values are rejected
before set membership. Verification returns accepted (0) or rejected (2);
malformed traces remain malformed (2). A rejected certificate is not necessarily
an unsafe trace. Recovery returns recovered (0), unsafe for an unreconstructible
well-formed selected state (1), or malformed image structure (2).

The fallback search compares candidates across **all** acknowledgements using
`(len(cut), tuple(sorted(cut)))`. A no-candidate-root acknowledgement contributes
its anchor closure to the incumbent; it does not end the search. The nonnegative
choice budget is shared across enumerated products. On exhaustion, `safe` and
`minimum_bad_cut` are null. A retained `bad_cut` and `bad_cut_ack_index` identify
only an already observed violation, not a certified global minimum.

There is no separate solver CLI. `solver.solve_model` calls the local Z3 C API;
library loading/evaluation failure raises `SolverUnavailable`, whereas an actual
Z3 `unknown` answer is returned as `status=unknown,safe=null`. Full reproduction
preflights the capability and fails before launching the campaign when absent;
core mode explicitly omits SMT and records the omission. Neither a missing
library nor an omitted query is recorded as a successful or unknown SMT query.
