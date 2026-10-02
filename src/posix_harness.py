"""Selector-based POSIX process-termination control.

This fixture uses CURRENT rather than greatest-durable-root selection. A hook
runs only after a complete temporary-write/fsync/rename/directory-fsync helper.
It is neither a refinement of the trace theorem nor a power-failure experiment.
"""
from __future__ import annotations
import hashlib, json, os, pathlib, shutil, sys
from dataclasses import dataclass
from typing import Any

CRASH_EXIT=86


def _canon(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"))+"\n").encode()

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _fsync_dir(path: pathlib.Path) -> None:
    fd=os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_json(path: pathlib.Path, obj: Any) -> None:
    tmp=path.with_name(path.name+'.tmp')
    with open(tmp,'wb') as f:
        f.write(_canon(obj)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path); _fsync_dir(path.parent)

def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))

@dataclass
class CrashHook:
    after: int | None
    count: int = 0
    def boundary(self, name: str) -> None:
        self.count += 1
        marker=os.environ.get('RF_BOUNDARY_LOG')
        if marker:
            with open(marker,'a',encoding='utf-8') as f:
                f.write(f"{self.count}\t{name}\n"); f.flush(); os.fsync(f.fileno())
        if self.after is not None and self.count == self.after:
            os._exit(CRASH_EXIT)


def init_store(d: pathlib.Path) -> None:
    d.mkdir(parents=True,exist_ok=True)
    payload=b'A'*32
    atomic_json(d/'slot-0.json', {'slot':0,'generation':0,'sha256':_sha(payload),'payload_hex':payload.hex()})
    atomic_json(d/'root-0.json', {'epoch':0,'objects':{'object':{'sha256':_sha(payload),'slot':0,'generation':0}}})
    atomic_json(d/'CURRENT', {'epoch':0})


def _slot(slot: int, generation: int, payload: bytes) -> dict[str, Any]:
    return {'slot':slot,'generation':generation,'sha256':_sha(payload),'payload_hex':payload.hex()}

def _root(epoch: int, slot: int, generation: int, payload: bytes) -> dict[str, Any]:
    return {'epoch':epoch,'objects':{'object':{'sha256':_sha(payload),'slot':slot,'generation':generation}}}


def update(d: pathlib.Path, protocol: str, crash_after: int | None) -> int:
    h=CrashHook(crash_after)
    b=b'B'*32
    c=b'C'*32
    if protocol=='unsafe':
        # Destructive reuse while CURRENT still selects root 0.
        atomic_json(d/'slot-0.json', _slot(0,1,b)); h.boundary('reuse-old-slot')
        atomic_json(d/'root-1.json', _root(1,0,1,b)); h.boundary('publish-new-root-record')
        atomic_json(d/'CURRENT', {'epoch':1}); h.boundary('select-new-root')
    elif protocol=='ordered':
        # Publish into a fresh generation/slot, select it, then retire old slot.
        atomic_json(d/'slot-1.json', _slot(1,0,b)); h.boundary('write-new-slot')
        atomic_json(d/'root-1.json', _root(1,1,0,b)); h.boundary('publish-new-root-record')
        atomic_json(d/'CURRENT', {'epoch':1}); h.boundary('select-new-root')
        atomic_json(d/'slot-0.json', _slot(0,1,c)); h.boundary('retire-and-reuse-old-slot')
    else:
        raise ValueError(protocol)
    return h.count


def recover(d: pathlib.Path) -> tuple[bool,str]:
    try:
        cur=read_json(d/'CURRENT')
        if type(cur.get('epoch')) is not int: return False,'bad-current'
        root=read_json(d/f"root-{cur['epoch']}.json")
        if root.get('epoch') != cur['epoch']: return False,'root-epoch'
        ref=root['objects']['object']
        if type(ref.get('slot')) is not int or type(ref.get('generation')) is not int:
            return False,'bad-ref'
        rec=read_json(d/f"slot-{ref['slot']}.json")
        if rec.get('slot')!=ref['slot'] or rec.get('generation')!=ref['generation']:
            return False,'stale-generation'
        payload=bytes.fromhex(rec['payload_hex'])
        if _sha(payload)!=rec.get('sha256') or rec.get('sha256')!=ref.get('sha256'):
            return False,'digest'
        return True,payload.decode('ascii')
    except Exception as e:
        return False,type(e).__name__


def child_main(argv: list[str]) -> int:
    d=pathlib.Path(argv[0]); protocol=argv[1]; after=int(argv[2]) if argv[2]!='none' else None
    update(d,protocol,after); return 0

if __name__=='__main__':
    raise SystemExit(child_main(sys.argv[1:]))
