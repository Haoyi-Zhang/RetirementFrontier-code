#!/usr/bin/env python3
"""Check exact result paths and primary-row consistency (not a proof)."""
from __future__ import annotations
import json
import pathlib
import re
import sys
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from strictjson import loads

def read(path: pathlib.Path):
    return loads(path.read_text(encoding='utf-8'))

def rows(root: pathlib.Path, name: str):
    return [loads(s) for s in (root/'results'/name).read_text().splitlines() if s.strip()]

def get(obj, path):
    for key in path.split('.'):
        obj = obj[key]
    return obj

def check(root: pathlib.Path = ROOT) -> dict:
    summary = read(root/'results/summary/summary.json')
    expected = {
        'census.graphs':33792, 'census.candidate_cuts':2129920,
        'census.safe_graphs':3072, 'census.consumer_checks':3072,
        'census.minimum_witness_checks':30720, 'census.disagreements':0,
        'heldout.cases':256, 'heldout.safe':75, 'heldout.unsafe':181,
        'heldout.consumer_checks':75, 'heldout.minimum_witness_checks':181,
        'heldout.disagreements':0, 'fallback.formulas':255,
        'fallback.assignments':2040, 'fallback.fixed_candidate_formulas':92,
        'fallback.fixed_candidate_oracle_checks':92,
        'fallback.minimum_witness_checks':92, 'fallback.disagreements':0,
        'scaling.instances':45, 'scaling.policy_rows':180,
        'scaling.max_events':5599, 'public.input_bytes':25582,
        'public.policy_rows':9, 'public.unchanged_across_selected_tags':True,
        'recovery.images':720, 'recovery.scaling_images':567,
        'recovery.public_images':153, 'recovery.unreachable_slots_total':3765,
        'tests.returncode':0, 'tests.skipped':0,
        'solver.unknown':0,
    }
    mode = read(root/'results/summary/execution-mode.json')
    # A core-only run must not masquerade as a full solver run.
    full = get(summary,'solver.queries') != 0
    expected['tests.skipped'] = 0 if full else 1
    expected.update({
        'solver.queries':65 if full else 0,
        'solver.pilot_queries':2 if full else 0,
        'solver.campaign_queries':67 if full else 0,
        'solver.heldout':32 if full else 0,
        'solver.scaling':30 if full else 0,
        'solver.public':3 if full else 0,
    })
    for field in ('idempotence','json_roundtrip','quiescent_reclamation','reference_multiplicity'):
        expected['recovery.validation_checks.'+field]=720
    checks=[]; errors=[]
    def require(label, okay, detail=None):
        checks.append({'check':label, 'passed':bool(okay), 'observed':detail})
        if not okay: errors.append(label)
    require('mode declaration matches solver execution', mode.get('mode') == ('full' if full else 'core-without-smt') and mode.get('smt_executed') is full and mode.get('returncode') == 0)
    for path,value in expected.items():
        try: actual=get(summary,path)
        except KeyError:actual=None
        require('summary.'+path,type(actual) is type(value) and actual==value,actual)
    # Preserve retained campaign metadata while allowing a rerun to include
    # additional regressions. Never substitute a source-era constant for the
    # number actually reported by that run's unittest output.
    tests=read(root/'results/pilots/unit-tests.json')
    count=tests.get('test_methods')
    ran=re.search(r'Ran (\d+) tests? in ',tests.get('stderr','')+tests.get('stdout',''))
    require('test count matches primary runner output and summary',
            type(count) is int and count>=75 and ran is not None
            and int(ran.group(1))==count and summary['tests']['methods']==count,count)
    require('test exit and skips match primary record',
            tests.get('returncode')==summary['tests']['returncode']
            and tests.get('skipped',0)==summary['tests']['skipped'])
    census=rows(root,'census/graphs.jsonl')
    variants=rows(root,'heldout/variants.jsonl')
    truth=rows(root,'fallback/truth-table.jsonl')
    fixed=rows(root,'fallback/fixed-candidate.jsonl')
    scaling=rows(root,'scaling/policy-metrics.jsonl')
    public=rows(root,'public/policy-metrics.jsonl')
    recovery=rows(root,'scaling/recovery.jsonl')+rows(root,'public/recovery.jsonl')
    require('census row count',len(census)==33792,len(census))
    require('census safe row count',sum(r['safe'] for r in census)==3072)
    require('census candidate sum',sum(r['candidate_cuts'] for r in census)==2129920)
    require('census oracle/decision correspondence',all(r['safe']==(r['bad_cuts']==0) for r in census))
    require('census all unsafe minimum checks',all(r['minimum_witness_agrees'] is True and r['minimum_bad_cut'] is not None for r in census if not r['safe']))
    require('census all positive consumers',all(r['consumer_accepted'] is True for r in census if r['safe']))
    require('retained variant dimensions',len(variants)==256 and len({r['seed'] for r in variants})==64)
    require('retained oracle/decision correspondence',all(r['safe']==(r['bad_cuts']==0) for r in variants))
    require('retained unsafe minimum checks',sum(not r['safe'] for r in variants)==181 and all(r['minimum_witness_agrees'] is True for r in variants if not r['safe']))
    require('retained positive consumers',all(r['consumer_accepted'] is True for r in variants if r['safe']))
    require('fallback truth-table equivalence',len(truth)==2040 and all(r['satisfies']==r['fallback_ack_violation'] for r in truth))
    require('fallback fixed-candidate minima',len(fixed)==92 and all(r['minimum_witness_agrees'] is True for r in fixed))
    require('scaling dimensions',len(scaling)==180 and len({(r['family'],r['size'],r['seed']) for r in scaling})==45)
    require('public policy dimensions',len(public)==9)
    require('all policy consumers accepted',all(r['consumer_rows']['accepted'] is True for r in scaling+public))
    require('serialized-write arithmetic',all(r['encoded_write_bytes']==r['data_record_bytes']+r['root_record_bytes']+r['free_record_bytes'] for r in scaling+public))
    # Certified is a copied storage trace, not another independent physical policy.
    by_key={(r['family'],r['size'],r['seed'],r['policy']):r for r in scaling}
    equality_fields=['events','roots','slots','logical_input_bytes','chunk_payload_bytes','manifest_payload_bytes','encoded_write_bytes']
    require('certified/unchecked physical trace identity',all(all(r[k]==by_key[(r['family'],r['size'],r['seed'],'unchecked-dedup')][k] for k in equality_fields) for r in scaling if r['policy']=='certified-dedup'))
    require('recovery count and occupancy',len(recovery)==720 and sum(r['unreachable_slots'] for r in recovery)==3765)
    require('all recovery checks executed',all(all(r[k] is True for k in ['idempotent_recovery_checked','json_roundtrip_checked','reference_multiplicity_checked','quiescent_reclamation_checked']) for r in recovery))
    # Count actual query sites, excluding the copied certified reporting rows.
    queried=[r['solver'] for r in variants+public+scaling if r.get('solver') is not None and r.get('policy')!='certified-dedup']
    require('primary SMT query count',len(queried)==(65 if full else 0),len(queried))
    require('primary SMT no unknown',all(r['status'] in ['sat','unsat'] for r in queried))
    posix=read(root/'results/posix-smoke.json') if (root/'results/posix-smoke.json').exists() else None
    require('POSIX evidence present and explicit', isinstance(posix,dict) and posix.get('ordered_failures')==0 and posix.get('unsafe_failures')==2 and len(posix.get('rows',[]))==11)
    require('POSIX observation accounting', posix is not None and sum(r.get('observation')=='initial-state' and r.get('child_returncode') is None for r in posix['rows'])==2 and sum(r.get('observation')=='normal-completion' and r.get('child_returncode')==0 for r in posix['rows'])==2 and sum(r.get('observation')=='injected-termination' and r.get('child_returncode')==86 for r in posix['rows'])==7)
    return {'schema':1,'mode':'full' if full else 'core-only','checks':checks,'errors':errors,
            'interpretation':'Consistency of explicit finite computational evidence; not a general proof, power-loss experiment, or publication decision.'}

def main():
    try: report=check()
    except (KeyError,ValueError,OSError,TypeError) as exc:
        print(f'Invalid or missing evidence: {exc}',file=sys.stderr);return 1
    print(json.dumps(report,indent=2))
    return 1 if report['errors'] else 0
if __name__=='__main__':raise SystemExit(main())
