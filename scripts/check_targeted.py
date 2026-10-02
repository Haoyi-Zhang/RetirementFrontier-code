#!/usr/bin/env python3
"""Execute explicit cases, both parsers, exhaustive cuts, and the public CLI."""
import sys,json,copy,subprocess,tempfile,time
from pathlib import Path
sys.dont_write_bytecode=True
r=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(r/'src'),str(r/'tests')]
from targeted_cases import *
from model import admit_trace
from verifier import _parse, verify_certificate
from checker import analyze_model
from fallback import fixed_candidate_model
from oracle import exhaustive_model,evaluate_cut
from medium import materialize,recover
out={'basis':'Current code; actual directed execution. Before-edit observations are retained separately.', 'cases':{},'cli':[]}
for name,t in cases().items():
 row={'trace':t}
 for parser,fn in [('producer',admit_trace),('consumer',_parse)]:
  try: fn(t); row[parser]={'admitted':True}
  except Exception as e: row[parser]={'admitted':False,'exception':type(e).__name__,'message':str(e)}
 if row['producer']['admitted']:
  m=admit_trace(t)
  row['strict']=analyze_model(m,minimum=True)
  row['fallback']=fixed_candidate_model(m)
  row['oracle_fallback']=exhaustive_model(m,True)
  row['cuts']=[{'cut':c,'verdict':evaluate_cut(m,c,True)} for mask in range(1<<m.n) if m.is_legal_cut(c:=[i for i in range(m.n) if mask>>i&1])]
  if name == 'canonical-witnesses':
   certificate=analyze_model(m)['certificate']
   row['certificate']=certificate
   row['canonical_accepted']=verify_certificate(t,certificate)['accepted']
   row['smaller_qualifying_roots']=[]
   for part,field in [('retirement','guard_root'),('acknowledgement','root')]:
    changed=copy.deepcopy(certificate);changed[part][0][field]=3
    try:
     verify_certificate(t,changed); result={'accepted':True}
    except Exception as e:
     result={'accepted':False,'exception':type(e).__name__,'message':str(e)}
    row['smaller_qualifying_roots'].append({'section':part,**result})
  if 'multiple' in name:
   reverse=copy.deepcopy(t);reverse['acknowledgements'].reverse()
   row['reordered_fallback']=fixed_candidate_model(admit_trace(reverse))
   row['budget_zero']=fixed_candidate_model(m,0)
   row['reordered_budget_zero']=fixed_candidate_model(admit_trace(reverse),0)
 out['cases'][name]=row
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'in.json'
 for mode in ['trace-role','trace-kind','image-role','image-kind']:
  for value in [[],{},None]:
   if mode.startswith('trace'):
    t=late_manifest('manifest');t['events'][1][mode.split('-')[1]]=value
    parsers={}
    for label,fn in [('producer',admit_trace),('consumer',_parse)]:
     try:fn(t);parsers[label]='accepted'
     except Exception as e:parsers[label]=type(e).__name__
    cmd='certify'
   else:
    t=materialize(admit_trace(late_manifest('manifest')),[0,1]);t['slots']['0'][mode.split('-')[1]]=value;parsers={};cmd='recover'
   p.write_text(json.dumps(t));cp=subprocess.run([sys.executable,str(r/'src/cli.py'),cmd,str(p)],capture_output=True,text=True)
   out['cli'].append({'mode':mode,'value':value,'parsers':parsers,'returncode':cp.returncode,'stdout':cp.stdout,'stderr':cp.stderr})
checks=[]
for name in ('late-manifest-wrong-role','late-chunk-wrong-role','late-chunk-wrong-object','late-manifest-noncanonical'):
 checks.append(all(out['cases'][name][p]['admitted'] is False for p in ('producer','consumer')))
for name in ('late-manifest-valid','late-chunk-valid','missing-manifest','missing-chunk','no-root-multiple-acks','mixed-multiple-acks','canonical-witnesses'):
 row=out['cases'][name]
 checks.append(row['producer']['admitted'] and row['consumer']['admitted'])
 checks.append(row['fallback']['safe']==row['oracle_fallback']['safe'])
 checks.append(row['fallback']['minimum_bad_cut']==row['oracle_fallback']['minimum_bad_cut'])
for row in out['cli']:
 checks.append(row['returncode']==2 and 'TypeError' not in row['stderr'] and json.loads(row['stdout'])['status']=='malformed')
out['all_directed_checks_passed']=all(checks)
out['directed_checks']=len(checks)
p=r/'results/targeted';p.mkdir(exist_ok=True)
(p/'binding-minimum-format-after.json').write_text(json.dumps(out,indent=2)+'\n')
for k,v in out['cases'].items():print(k,v['producer'], v.get('fallback',{}).get('safe'), v.get('fallback',{}).get('minimum_bad_cut'),v.get('oracle_fallback',{}).get('safe'),v.get('oracle_fallback',{}).get('minimum_bad_cut'))
for v in out['cli']:print(v['mode'],repr(v['value']),v['parsers'],'rc',v['returncode'],'TypeError' in v['stderr'])

if not out["all_directed_checks_passed"]: raise SystemExit(1)
