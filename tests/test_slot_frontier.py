"""Owned finite strict-root regressions; no solver, disk or process workflow."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from checker import analyze_model, pair_analyze_model
from engine import census_template, build_trace, strip_build_metrics, heldout_operations
from model import admit_trace
from oracle import exhaustive_model
from targeted_cases import canonical_witness_trace, data, ref, root, trace
from verifier import verify_certificate, VerificationError


def tiny_cases():
    for mask in range(1024):
        yield census_template('unretired-reuse', mask)
    for mask in range(0, 32768, 257):
        yield census_template('retired-free', mask)


def controls():
    base = canonical_witness_trace()
    yield 'canonical', base
    repeated = deepcopy(base)
    repeated['events'] += [dict(id=7, kind='free', slot=0, generation=1, deps=[5]),
                           data(8, 0, b'{}', 'manifest', [5], generation=2)]
    yield 'repeated-free-and-equal-bytes-new-generation', repeated
    namespace = {'x': [ref(0, b'a')]}
    manifest = json.dumps(namespace, sort_keys=True, separators=(',', ':')).encode()
    yield 'later-user-after-free', trace(
        [dict(op='put', name='x', chunks_hex=['61'])] * 2,
        [data(0, 0, b'a'), data(1, 1, manifest, 'manifest'),
         root(2, 1, namespace, ref(1, manifest), {'x': '61'}, [0, 1]),
         dict(id=3, kind='free', slot=0, generation=1, deps=[2]),
         data(4, 2, manifest, 'manifest'),
         root(5, 2, namespace, ref(2, manifest), {'x': '61'}, [0, 2, 4])], [])
    yield 'unused-allocations-and-frees', trace([], [data(0, 0, b'a'),
        dict(id=1, kind='free', slot=0, generation=1, deps=[]),
        data(2, 0, b'a', generation=2),
        dict(id=3, kind='free', slot=0, generation=2, deps=[])], [])
    yield 'empty', trace([], [], [])


def definition(model):
    """Enumerate historical allocation/ref pairs and fresh set reachability.

    No running frontier, producer closure bitsets or consumer helper is used.
    """
    def closure(seed):
        pending, seen = list(seed), set()
        while pending:
            i = pending.pop()
            if i not in seen:
                seen.add(i)
                pending.extend(model.events[i]['deps'])
        return seen
    publication, publication_failures = [], []
    for r in model.roots:
        for index, reference in enumerate(model.refs_by_root[r]):
            key = tuple(reference[k] for k in ('slot', 'generation', 'digest', 'length'))
            allocations = [i for i, e in enumerate(model.events) if e['kind'] == 'data'
                           and tuple(e[k] for k in ('slot', 'generation', 'digest', 'length')) == key]
            a = allocations[0] if allocations else None
            row = dict(root=r, ref_index=index, allocation=a)
            (publication if a is not None and a in closure([r]) else publication_failures).append(row)
    retirement, retirement_failures, obligations = [], [], 0
    for slot in sorted({e['slot'] for e in model.events if e['kind'] in ('data', 'free')}):
        for w, writer in enumerate(model.events):
            if writer['kind'] not in ('data', 'free') or writer['slot'] != slot:
                continue
            users = []
            for a, allocation in enumerate(model.events[:w]):
                if allocation['kind'] == 'data' and allocation['slot'] == slot:
                    key = tuple(allocation[k] for k in ('slot', 'generation', 'digest', 'length'))
                    users += [r for r in model.roots for reference in model.refs_by_root[r]
                              if tuple(reference[k] for k in ('slot', 'generation', 'digest', 'length')) == key]
            if users:
                obligations += 1
                frontier = max(users)
                guard = max((r for r in model.roots if r in closure([w])), default=-1)
                if guard <= frontier:
                    retirement_failures.append(dict(writer=w, frontier=frontier, guard=guard))
                else:
                    retirement.append(dict(writer=w, guard_root=guard))
    acknowledgement, acknowledgement_failures = [], []
    for index, ack in enumerate(model.acknowledgements):
        if ack['prefix'] == 0:
            acknowledgement.append(dict(ack_index=index, root=-1))
        else:
            qualifying = [r for r in model.roots if r in closure(ack['anchors'])
                          and model.events[r]['prefix'] >= ack['prefix']]
            if qualifying:
                acknowledgement.append(dict(ack_index=index, root=max(qualifying)))
            else:
                acknowledgement_failures.append(dict(ack_index=index, prefix=ack['prefix']))
    safe = not (publication_failures or retirement_failures or acknowledgement_failures)
    certificate = dict(schema='retirement-frontier-certificate-v1', publication=publication,
                       retirement=retirement, acknowledgement=acknowledgement) if safe else None
    return dict(safe=safe, publication_failures=publication_failures,
                retirement_failures=retirement_failures, acknowledgement_failures=acknowledgement_failures,
                frontier_obligations=obligations, certificate=certificate)


class SlotFrontierTests(unittest.TestCase):
    def test_dense_definition_and_independent_pair_baseline(self):
        for t in list(tiny_cases()) + [t for _, t in controls()]:
            saved = deepcopy(t)
            model = admit_trace(t)
            report = analyze_model(model, minimum=True)
            expected = definition(model)
            self.assertEqual({key: report[key] for key in expected}, expected)
            pair = pair_analyze_model(model, minimum=True)
            self.assertEqual((report['safe'], report['minimum_bad_cut']), (pair['safe'], pair['minimum_bad_cut']))
            if report['safe']:
                self.assertTrue(verify_certificate(t, report['certificate'])['accepted'])
            else:
                with self.assertRaises(VerificationError):
                    verify_certificate(t, dict(schema='retirement-frontier-certificate-v1', publication=[], retirement=[], acknowledgement=[]))
            self.assertEqual(t, saved)

    def test_small_all_cut_recovery_minima(self):
        selected = [census_template('retired-free', mask) for mask in (0, 1, 257, 1023, 32767)]
        selected += [t for _, t in controls()]
        for t in selected:
            model = admit_trace(t)
            report = analyze_model(model, minimum=True)
            exact = exhaustive_model(model, max_events=9)
            self.assertEqual((report['safe'], report['minimum_bad_cut']), (exact['safe'], exact['minimum_bad_cut']))
        later = analyze_model(admit_trace(dict(controls())['later-user-after-free']))
        self.assertEqual(later['retirement_failures'], [dict(writer=3, frontier=5, guard=2)])

    def test_generated_traces_and_canonical_witness_rejection(self):
        for seed in range(10000, 10016):
            for deduplicate in (False, True):
                t = strip_build_metrics(build_trace(heldout_operations(seed), deduplicate=deduplicate, batch_size=2))
                report = analyze_model(admit_trace(t))
                self.assertEqual({key: report[key] for key in definition(admit_trace(t))}, definition(admit_trace(t)))
                self.assertTrue(verify_certificate(t, report['certificate'])['accepted'])
        t = canonical_witness_trace()
        cert = analyze_model(admit_trace(t))['certificate']
        self.assertEqual(cert['retirement'], [dict(writer=6, guard_root=5)])
        for section, field in (('retirement', 'guard_root'), ('acknowledgement', 'root')):
            changed = deepcopy(cert)
            changed[section][0][field] = 3
            with self.assertRaises(VerificationError):
                verify_certificate(t, changed)


if __name__ == '__main__':
    unittest.main()
