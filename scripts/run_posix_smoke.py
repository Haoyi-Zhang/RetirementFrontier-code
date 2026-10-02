#!/usr/bin/env python3
"""Enumerate all named process-crash prefixes of the POSIX harness."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import json, os, pathlib, shutil, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from posix_harness import CRASH_EXIT, init_store, recover, update
OUT=ROOT/'results'/'posix-smoke.json'

def boundary_count(protocol: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        d=pathlib.Path(td); init_store(d)
        return update(d,protocol,None)

def main() -> int:
    rows=[]
    with tempfile.TemporaryDirectory() as base_td:
        base=pathlib.Path(base_td)/'base'; init_store(base)
        for protocol in ('unsafe','ordered'):
            n=boundary_count(protocol)
            for after in range(0,n+2):
                with tempfile.TemporaryDirectory() as td:
                    d=pathlib.Path(td)/'store'; shutil.copytree(base,d)
                    if after==0:
                        rc=None  # Initial-state control: no child process was launched.
                    else:
                        cp=subprocess.run([sys.executable,str(ROOT/'src'/'posix_harness.py'),
                                           str(d),protocol,str(after)],
                                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        rc=cp.returncode
                    ok,value=recover(d)
                    rows.append({'protocol':protocol,'crash_after_boundary':after,
                                 'child_returncode':rc,'observation':('initial-state' if after==0 else 'normal-completion' if rc==0 else 'injected-termination' if rc==CRASH_EXIT else 'unexpected-exit'),'recoverable':ok,'recovered':value})
    payload={'schema':1,
             'scope':'process termination after complete atomic-file replacement helpers; explicit CURRENT selector, not strict greatest-root refinement or power-loss testing',
             'rows':rows,
             'ordered_failures':sum(not r['recoverable'] for r in rows if r['protocol']=='ordered'),
             'unsafe_failures':sum(not r['recoverable'] for r in rows if r['protocol']=='unsafe')}
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k!='rows'},sort_keys=True))
    if any(r['observation']=='unexpected-exit' for r in rows): return 1
    if payload['ordered_failures'] != 0: return 1
    if payload['unsafe_failures'] < 1: return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
