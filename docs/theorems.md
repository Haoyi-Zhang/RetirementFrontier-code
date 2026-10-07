# Retirement-frontier certificates: model and proofs

These are mathematical arguments, not machine-checked proofs. Executable finite checks are a separate form of evidence. Every theorem below quantifies over the admitted finite trace model, not physical devices or all execution paths of an unverified program.

## 1. Admitted traces and safety

A trace has n atomic events, numbered in issue order 0,...,n-1. Each persistence dependency of event i names an event j<i. Let D(i) be i and all its dependency ancestors; D(X) is the union of D(i) over i in X. A cut I is legal precisely when i in I implies D(i) is a subset of I. There are no additional hidden ordering or progress requirements. The complete trace and dependency relation are trusted observations supplied to the checker. Proving that a real storage stack refines these observations is a separate, unfulfilled obligation.

A DATA event atomically replaces one mutable physical slot with a generation, a finite byte string, its digest, and its length. Every (slot,generation) pair occurs in at most one DATA event. Generations need not be contiguous; they cannot wrap or repeat. A FREE event destructively empties its slot; its generation field does not make it a conditional compare-and-delete. All present writers to a slot are interpreted in issue order, so its value is determined by the last present writer. A later incarnation with identical bytes is still a different allocation. This model is stronger than content equality alone. Digest collisions among actual and expected consumed bytes are detected on trace admission, rather than excluded by an unproved assertion about SHA-256.

A ROOT is an immutable, append-only commit marker containing an increasing operation-prefix number and a manifest reference. The referenced manifest is itself a DATA allocation. It serializes a flat mapping from object names to ordered chunk references. A reference consists of (slot,generation,digest,length). Each ROOT also has a ghost namespace statement in the trace, checked against the prefix of client put/delete operations and the digest of the canonical manifest. This statement is **not** part of the materialized storage image. Recovery obtains the namespace by reading actual manifest bytes, not from the ghost statement. Distinct references to the same allocation give one physical safety obligation but retain their multiplicity for object reconstruction and derived reference counts.

Strict recovery chooses the greatest present ROOT by issue order. It either reconstructs all of that root's references, including the manifest, or reports failure; it does not skip an invalid root. With no root it returns the initial empty namespace. Metadata-record atomicity includes the complete flat manifest, independent of its size: this is a declared model assumption, not an implementation of hardware atomicity for large writes.

An acknowledgement is (issued,p,A): an issue horizon, an operation prefix p, and a set A of required durable anchors before the horizon. Its compatible cuts are legal cuts containing A. The lower-bound durability obligation is that every such cut recover a legal prefix at least p. Unacknowledged later operations may have committed. A pending overwrite or deletion may therefore supersede previously acknowledged data. Safety does not require retaining all historical acknowledged versions forever.

All-cut safety means (i) strict reconstruction succeeds for every legal cut, with the namespace of its selected legal operation prefix, and (ii) each compatible cut satisfies every applicable acknowledgement's prefix lower bound. There is no eventual-persistence or crash-frequency assumption.

Binding admission is over the complete finite trace. Both parsers first index every DATA identity, then check each root's present manifest allocation for manifest role and exact canonical bytes, every present object allocation for chunk role, and each completely bound object's concatenation against its ghost value. Forward allocations are admissible when correctly bound; backward-only dependencies then make P fail if the referring root cannot force them. A truly absent allocation remains an admitted safety failure. Thus a role or byte-binding parser defect is not by itself a counterexample to the strict P/D/A theorem.

## 2. Closure lemma

**Lemma 1.** D(X) is the unique inclusion-minimum legal cut containing X. A legal cut containing X and excluding a set Y exists if and only if D(X) intersects Y in the empty set.

**Proof.** Induction on event number shows that each D(i) contains all the ancestors of every one of its members. Unions preserve this property, so D(X) is legal and contains X. Every legal cut containing X must, by repeated application of dependency closure, contain D(X). Therefore a disjoint D(X) is a witness; a nondisjoint one is an obstruction contained in every candidate cut. Uniqueness of the minimum follows from these inclusions. This lemma is elementary dependency reasoning, not a novelty claim. QED.

## 3. Exact retirement-frontier characterization

For a reference in root r, let a be its matching unique DATA event, or undefined. Let U(a) be the set of roots referencing allocation a and L(a)=max U(a), with the maximum of an empty set equal to -1. For every mutable-slot writer w, define

    T(w) = max { L(a) : a < w, a is DATA, slot(a)=slot(w), U(a) nonempty },
    F(w) = max { q : q is ROOT and q belongs to D(w) }.

Both maxima use issue IDs, not operation-prefix values. They coincide in ordering because ROOT prefixes strictly increase. A writer's own allocation does not contribute to T(w). T(w) can exceed w in an arbitrary invalid trace: a future root may incorrectly refer to a destroyed earlier generation. Such a trace must be rejected.

**Theorem 2 (exact safety).** An admitted trace is all-cut safe under strict recovery if and only if all three conditions hold:

P. Every reference in every root r has a matching a in D(r).

D. For every writer w with T(w)>=0, F(w)>T(w).

A. For every acknowledgement (issued,p,A), D(A) contains a root with operation prefix at least p, unless p=0.

**Necessity of P.** Suppose some root r refers to allocation a absent from D(r), or to an allocation that does not exist. The cut D(r) selects r, since all dependency ancestors have IDs at most r. Its reference cannot resolve: no different DATA event may use a's allocation identity, and a FREE is not a matching DATA event. Hence strict reconstruction fails. This remains a counterexample even if other references are also broken.

**Necessity of D, given P.** Let w have T(w)>=0 but F(w)<=T(w). Select an earlier allocation a to the same slot whose last user is r=T(w). P forces a into D(r). By Lemma 1, I=D(r) union D(w) is legal. Its greatest ROOT is r, because D(r) contains no later root and the greatest ROOT in D(w) is at most r. It contains both a and the destructive writer w>a. The last present writer to that slot is at least w, and therefore cannot have a's unique allocation identity. Root r cannot reconstruct. This includes slot reuse, stale-generation FREE records, and destruction of manifest allocations.

**Necessity of A.** If A fails, its closure D(A) contains no qualifying ROOT. It is a compatible legal cut. If it is not reconstructible, safety already fails; otherwise its selected prefix is below p. No event after the acknowledgement horizon is needed for this witness because anchors and their ancestors precede that horizon.

**Sufficiency of P and D.** Let I be a legal cut and r its greatest ROOT. If no ROOT is present, empty recovery succeeds. Otherwise take any reference of r. P gives its unique DATA event a in D(r), so a is in I. If the slot did not hold a, some present writer w>a would be the last writer to that slot. Then T(w)>=L(a)>=r. D implies a ROOT q=F(w)>T(w)>=r in D(w), hence in I. This contradicts r being greatest. Thus every reference is intact. Manifest binding, admission's exact-byte comparison, and reference identity imply that parsing and concatenation recover the declared legal namespace. Repetition of chunks affects multiplicity, not this argument.

**Sufficiency of A.** Every compatible I contains D(A). Therefore it contains a qualifying ROOT q. Its greatest ROOT has prefix at least q's prefix, since prefixes increase with issue order; the preceding reconstruction argument makes that prefix valid. QED.

The general theorem is independent of n. Executable implementations use finite machine inputs and checked 63-bit bounds; they are not proof assistants. A bitset implementation stores n closures and takes quadratic bits in the worst case. Computing the scalar frontier is not the same as reducing all analysis to linear memory.

## 4. Canonical certificates and the code-separated consumer

A positive certificate has three ordered lists: one matching allocation ID for every distinct reference of every ROOT; exactly F(w) for each writer with T(w)>=0; and the greatest ROOT in each positive acknowledgement anchor closure (or -1 for a zero-prefix acknowledgement). Every list has exact coverage: missing and extra entries are rejected. The consumer independently checks syntax, byte bindings, operation prefixes, allocation uniqueness, membership in the declared dependency DAG, retirement inequalities against all historical users of the slot, and acknowledgement coverage. It does not import the producer, engine or concrete oracle.

**Corollary 3.** A consumer that performs these checks accepts precisely all-cut-safe admitted traces for which a valid positive certificate is supplied; every safe admitted trace has such a certificate.

**Proof.** Accepted allocation IDs establish P. Each guarding ROOT is a dependency ancestor greater than every last user of every earlier same-slot allocation, establishing D. Each acknowledgement witness establishes A. Apply Theorem 2. Conversely, when the conditions hold, choose the unique matching allocation IDs, choose F(w) for every retirement row, and choose the greatest ROOT in each positive anchor closure. Increasing root prefixes guarantee it qualifies; prefix zero uses -1. Produce rows in the exact traversal specified in formats.md. The resulting arrays equal the consumer's derived arrays element by element; arbitrary smaller qualifying witnesses do not satisfy this canonical-format contract. QED.

If R is the number of distinct root references, W the number of mutable writers and B the number of acknowledgements, the certificate contains at most R+W+B event IDs, or O((R+W+B) log(n+1)) bits in a packed representation. The actual JSON representation is measured separately and is not bit-optimal. The consumer retains linear auxiliary graph state. It computes the greatest root ancestor of every event in one topological pass for retirement and acknowledgement checks, while publication witnesses are checked by backward reachability searches; runtime can therefore still be superlinear in the explicit trace. The two implementations are separated in code, not independently authored. Neither is claimed mechanically verified.

## 5. Minimum-cardinality counterexamples

Generate three kinds of candidate cuts:

1. D(r) for every publication failure.
2. D(r) union D(w), for every published reference allocation a in root r and every same-slot writer w>a with F(w)<=r.
3. D(A) for every failed acknowledgement condition.

**Theorem 4.** An unsafe admitted trace has a minimum-cardinality bad legal cut among these candidates. Taking the minimum cardinality (with any deterministic tie-break) is exact.

**Proof.** Each candidate is legal by Lemma 1 and bad by the necessity proofs. Now let I be any bad legal cut. If I is reconstructible but violates an acknowledgement, it contains D(A) of a failed A condition: a qualifying ROOT in D(A) would also be in I and prevent the violation. Otherwise take a broken reference in the selected ROOT r. If publication fails for this reference, D(r) is a bad candidate contained in I. If publication holds, its allocation a is present; breakage requires a later present writer w>a in its slot. Since I selects r, every ROOT in D(w) is at most r, so F(w)<=r. The corresponding pair candidate is contained in I. Thus every bad cut contains a bad candidate, and no bad cut can be smaller than the minimum candidate. QED.

The compact T(w) witness suffices for a decision but need not be globally minimum. The optional minimum-witness mode therefore enumerates root/writer pairs rather than pretending that a single maximum preserves the optimization objective. Minimum means number of durable events, **not** shortest client program, minimum removed edges, minimum storage corruption, or a minimum positive certificate. Malformed inputs have syntax errors rather than certified crash counterexamples.

## 6. Recovery and reclamation corollaries

For a successful strict recovery, call a physical DATA slot reachable when it is the selected manifest slot or appears among that manifest's chunk references. Derived reference counts are the multiplicities of chunk references by allocation identity. They do not include the metadata manifest as an object chunk.

**Corollary 5 (quiescent reclamation).** With no concurrent writes or readers retaining historical snapshots, physically erasing any subset of the occupied unreachable DATA slots preserves the selected prefix and every recovered object. Repeating read-only recovery is idempotent.

**Proof.** Before reclamation, every represented record validates and successful recovery identifies the selected manifest and referenced object allocations. An occupied slot classified as unreachable contains none of those allocations. Removing any subset of such records leaves every root marker and selected read unchanged; revalidating the reduced image therefore yields the same manifest, prefix, and objects. Read-only recovery makes no mutations, so repeating it returns the same result. QED.

A one-pass scan of the materialized image plus decoding the selected namespace has O(S+K+B) scanning and byte-processing work, where S is the represented slot/tombstone and ROOT-record count, K is the number of selected reference occurrences, and B is the number of bytes read, including repeated chunk reads. Ordering and canonicalization are additional: the prototype sorts ROOT markers, occupied-slot reports and object names and canonicalizes the selected manifest. The scanning expression is not an end-to-end linear-time bound for this Python implementation. The implementation retains historical ROOT markers; therefore S can grow with history even if the live namespace is constant. This is not a history-independent recovery bound, nor an implementation of resumable writes after arbitrary repeated crashes.

The mathematical complexity family uses binary-encoded natural identifiers and unbounded explicit content labels, which can encode the exact bytes themselves. It does not fix the width of a cryptographic digest. The concrete implementation uses SHA-256 indexing with rejection of collisions among consumed bytes, and admits only 63-bit slot and generation identifiers. The asymptotic hardness statement is not about a literally fixed finite identifier or digest universe. This representation distinction is necessary for the unbounded reduction; finite reduction tests use the concrete admitted representation.

## 7. Why fallback changes the decision problem

Define alternative recovery as the greatest present ROOT whose manifest and all object references validate, skipping invalid roots and returning the empty namespace when none validates. Its acknowledgement question is different: an invalid newer root can be harmless if an older qualifying one survives.

**Theorem 6 (fallback boundary).** Deciding whether every acknowledgement-compatible cut recovers a prefix at least p under this fallback rule is coNP-complete for finite, explicitly represented traces with an unbounded number of candidate roots. Hardness holds with one acknowledgement, flat manifests, two DATA incarnations per variable slot, no FREE events, and clauses of three distinct variables.

**Membership.** A violating cut is a bit vector of n events. Checking its dependency closure, anchors, concrete slot image, validity of each ROOT, and the maximum valid prefix takes polynomial time in the explicit input byte size. Thus unsafety is in NP and safety is in coNP.

**Reduction.** Let phi be a 3-CNF formula on variables x_1,...,x_v with nonempty clauses C_1,...,C_m. For each variable make a physical slot with initial DATA f_i (generation 1, bytes encoding x_i=0) and a later optional DATA t_i (generation 2, distinct bytes encoding x_i=1). Set t_i's only predecessor to f_i. For each clause C_j, create a separate manifest slot and ROOT r_j with prefix j. The manifest contains one object's ordered references to the falsifying values of its literals: f_i for a positive x_i, t_i for a negative literal. Its client operation puts exactly that byte concatenation under the same object name, replacing the prior value. Each ROOT thus declares a semantically legal, different operation prefix. It depends on its own manifest allocation, not on the referenced variable DATA events. All ROOT markers are retained.

The single acknowledgement promises prefix 1 and anchors every f_i and every ROOT. Dependency closure forces all manifests. The only optional events are the t_i. Consequently its compatible cuts correspond bijectively to Boolean assignments: t_i present means x_i=true. A clause ROOT validates if and only if all its literals are false. There is a valid ROOT with prefix at least 1 if and only if some clause is false. Fallback violates the acknowledgement if and only if every clause is true, that is, phi is satisfiable. The construction has 2v+2m events, three references per clause plus a manifest reference, and polynomial encoding length. A SAT decision therefore reduces to unsafety, proving coNP-hardness of safety; combined with membership, this proves the theorem. QED.

The construction intentionally admits traces violating strict publication: strict analysis would reject many of them. It is a complexity result about arbitrary finite fallback traces, not a claim that the provided correct engine is hard to verify. It does not establish hardness for two-superblock recovery or another fixed bound on candidate roots. The finite 255-formula test checks the executable reduction and an independent truth evaluator, not the asymptotic theorem.

## 8. A fixed-candidate fallback algorithm

The unbounded-candidate lower bound does not justify treating a small fixed root set as intractable. For an acknowledgement of prefix p>0, retain its k candidate ROOTs with prefix at least p. A root r is invalid exactly when one of the following literals holds: r is absent; a required unique allocation a is absent; or a same-slot writer w>a is present. A reference having no matching allocation makes the whole invalidity clause true. This includes the manifest allocation. The admitted unique-incarnation and byte-binding assumptions are essential to this literal representation.

For every candidate root choose one of these fault literals. A chosen positive writer requires its entire dependency closure; a chosen absence forbids that event. Let P be the required anchors plus all chosen positive writers, and let N be all chosen absent events. This choice has a compatible legal cut exactly when D(P) is disjoint from N. A successful choice has the explicit cut D(P).

**Theorem 7 (fixed-candidate fallback).** Enumerating all such choices decides fallback acknowledgement safety exactly. If L is the maximum number of fault literals for a candidate root, there are at most L^k choices. Straightforward graph searches give O(L^k (n+m+k)) time after reference/writer enumeration, where m is the number of dependency edges. For fixed k this is polynomial in the explicit input size; no fixed-parameter tractability claim is made. The minimum-cardinality D(P) over feasible choices is a globally minimum violating cut. Multiple acknowledgements are analyzed separately and their minimum witnesses compared with the incumbent, using (cardinality, sorted event IDs). An acknowledgement with no qualifying root contributes D(A); it does not justify an early return before the remaining acknowledgements are analyzed.

**Proof.** Every feasible choice has all anchors present and invalidates every qualifying root by its selected fault literal, so fallback has no valid prefix at least p. Conversely, in any compatible violating cut I every qualifying root is invalid. Select an actual fault literal from each root. Positive selections and anchors belong to I; negative selections are absent. Closure gives D(P) contained in I and disjoint from N. Thus at least one enumerated choice is feasible whenever a violation exists. Moreover its canonical cut is no larger than I, proving the minimum-cardinality result. There are at most L^k choices; each closure and disjointness check has the claimed elementary bound. QED.

The implementation enforces an explicit choice budget. Exhausting it returns `unknown`, not `safe`; no partial search is advertised as proving minimum cardinality. The budget is shared across acknowledgement choice products. On exhaustion, minimum_bad_cut is null; an optional bad_cut incumbent has no minimality claim. This checker shares input-admission helpers with the strict analyzer and is not the separate certificate consumer.

## 9. Exact last-user aggregation and its cost boundary

ROOT identifiers are totally ordered by the serial event order; this is not a theorem about maximal antichains in an arbitrary persistence poset. For an allocation a, let L(a) be its greatest referring ROOT or -1. For a writer w, let T(w) be the maximum L(a) over earlier allocations to w's slot and let F(w) be the greatest ROOT in the dependency closure of w, using -1 when absent. The pairwise retirement obligations require F(w)>r for every referring ROOT r of every earlier allocation threatened by w. Their conjunction is exactly F(w)>T(w) when T(w)>=0. When T(w)=-1 there is no retirement obligation.

**Proof.** Every threatened r is at most its allocation's L(a), which is at most T(w). Therefore F(w)>T(w) implies all pairwise inequalities. Conversely, the maximum defining T(w) is attained in the finite set of threatened ROOTs, so the corresponding pairwise inequality implies F(w)>T(w). QED.

The elementary maximum identity is not claimed as a new generic order-theoretic result. Its role is to compress the exact storage-lifetime obligations proved above. The current producer and consumer parse every reference before independently scanning each slot's writers with a running maximum: immediately before w it equals the maximum last user of earlier same-slot DATA allocations. The empty maximum is -1; only after checking w is its own last user added, and FREEs do not clear history. Induction over writers proves equality with the displayed definition. This component takes one maximum update per DATA allocation, but transitive closures can require quadratic bits and other checks remain superlinear. No unconditional total-analysis or measured end-to-end speedup follows. Minimum negative witnesses still enumerate all relevant root/writer pairs rather than only the maximum, because the maximum-root witness need not minimize durable-event cardinality.

## Content identities and hash collisions

The mathematical model uses exact content identities. The executable artifact represents them with SHA-256 and rehashes the recovered bytes. This tests accidental corruption and ordinary identifier consistency; it is not a proof that SHA-256 is injective. Deliberately constructed digest collisions are outside the theorem's implementation refinement unless a deployment compares full bytes or strengthens the identifier.
