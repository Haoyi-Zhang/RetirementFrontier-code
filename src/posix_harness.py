"""POSIX replacement helpers and two explicit recovery policies.

The original recovery policy uses CURRENT rather than greatest-durable-root
selection. Its CrashHook runs only after a complete
temporary-write/fsync/rename/directory-fsync helper.
selector_bridge projects these fixed completed-helper prefixes after initialization
to strict latest-publication recovery. This is not an arbitrary POSIX refinement
or a power-failure experiment. Optional event hooks expose successful helper
I/O boundaries, including in-helper ones, without changing the default protocol.
recover_latest instead treats retained root files as publications and ignores
CURRENT. Tests of supplied durable images do not establish filesystem durability.
"""
from __future__ import annotations
import hashlib, json, os, pathlib, shutil, sys
from dataclasses import dataclass
from typing import Any, Callable

CRASH_EXIT=86
EventHook = Callable[[pathlib.Path, str], None]


def _canon(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"))+"\n").encode()

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _fsync_dir(path: pathlib.Path, emit: Callable[[str], None] | None = None) -> None:
    fd=os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        if emit is not None: emit('dir-opened')
        os.fsync(fd)
        if emit is not None: emit('dir-synced')
    finally: os.close(fd)
    if emit is not None: emit('dir-closed')

def atomic_json(path: pathlib.Path, obj: Any, *, event_hook: EventHook | None = None) -> None:
    """Notify only after successful operations; hook exceptions stop the caller.

    A notification is a control-flow boundary, not a durability assertion.
    Hooks are optional and neither persisted nor included in CrashHook counts.
    """
    def emit(stage: str) -> None:
        if event_hook is not None: event_hook(path, stage)

    tmp=path.with_name(path.name+'.tmp')
    with open(tmp,'wb') as f:
        f.write(_canon(obj)); emit('file-written')
        f.flush(); emit('file-flushed')
        os.fsync(f.fileno()); emit('file-synced')
    emit('file-closed')
    os.replace(tmp,path); emit('replaced')
    _fsync_dir(path.parent, emit)
    emit('helper-complete')

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


def init_store(d: pathlib.Path, *, event_hook: EventHook | None = None) -> None:
    d.mkdir(parents=True,exist_ok=True)
    payload=b'A'*32
    atomic_json(d/'slot-0.json', {'slot':0,'generation':0,'sha256':_sha(payload),'payload_hex':payload.hex()}, event_hook=event_hook)
    atomic_json(d/'root-0.json', {'epoch':0,'objects':{'object':{'sha256':_sha(payload),'slot':0,'generation':0}}}, event_hook=event_hook)
    atomic_json(d/'CURRENT', {'epoch':0}, event_hook=event_hook)


def _slot(slot: int, generation: int, payload: bytes) -> dict[str, Any]:
    return {'slot':slot,'generation':generation,'sha256':_sha(payload),'payload_hex':payload.hex()}

def _root(epoch: int, slot: int, generation: int, payload: bytes) -> dict[str, Any]:
    return {'epoch':epoch,'objects':{'object':{'sha256':_sha(payload),'slot':slot,'generation':generation}}}


def update(d: pathlib.Path, protocol: str, crash_after: int | None, *, event_hook: EventHook | None = None) -> int:
    h=CrashHook(crash_after)
    b=b'B'*32
    c=b'C'*32
    if protocol=='unsafe':
        # Destructive reuse while CURRENT still selects root 0.
        atomic_json(d/'slot-0.json', _slot(0,1,b), event_hook=event_hook); h.boundary('reuse-old-slot')
        atomic_json(d/'root-1.json', _root(1,0,1,b), event_hook=event_hook); h.boundary('publish-new-root-record')
        atomic_json(d/'CURRENT', {'epoch':1}, event_hook=event_hook); h.boundary('select-new-root')
    elif protocol=='ordered':
        # Publish into a fresh generation/slot, select it, then retire old slot.
        atomic_json(d/'slot-1.json', _slot(1,0,b), event_hook=event_hook); h.boundary('write-new-slot')
        atomic_json(d/'root-1.json', _root(1,1,0,b), event_hook=event_hook); h.boundary('publish-new-root-record')
        atomic_json(d/'CURRENT', {'epoch':1}, event_hook=event_hook); h.boundary('select-new-root')
        atomic_json(d/'slot-0.json', _slot(0,1,c), event_hook=event_hook); h.boundary('retire-and-reuse-old-slot')
    else:
        raise ValueError(protocol)
    return h.count


def _recover_root(d: pathlib.Path, root: Any, epoch: int) -> tuple[bool, str]:
    if root.get('epoch') != epoch: return False,'root-epoch'
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


def recover(d: pathlib.Path) -> tuple[bool,str]:
    """Original CURRENT policy, retained for the existing smoke controls."""
    try:
        cur=read_json(d/'CURRENT')
        if type(cur.get('epoch')) is not int: return False,'bad-current'
        root=read_json(d/f"root-{cur['epoch']}.json")
        return _recover_root(d, root, cur['epoch'])
    except Exception as e:
        return False,type(e).__name__


def recover_latest(d: pathlib.Path) -> dict[str, Any]:
    """Strict greatest retained root-file recovery for the owned fixture.

    Epoch order represents publication order; epoch e maps to prefix e+1.
    Each root file combines the manifest and marker. Temporary files are not
    publications. The supplied directory must represent the retained image;
    this routine cannot distinguish a durable rename from a merely visible one.
    A broken newest root fails rather than falling back to an older root.
    """
    epoch = None
    try:
        epochs = []
        for path in d.glob('root-*.json'):
            token = path.name[5:-5]
            if (not token.isascii() or not token.isdecimal()
                    or len(token) > 19 or str(int(token)) != token
                    or int(token) >= (1 << 63) - 1):
                raise ValueError('invalid root filename')
            epochs.append(int(token))
        if not epochs:
            return {'ok': True, 'root_epoch': None, 'prefix': 0, 'objects_hex': {}}
        epoch = max(epochs)
        root = read_json(d/f'root-{epoch}.json')
        if type(root.get('epoch')) is not int or root['epoch'] != epoch:
            return {'ok': False, 'root_epoch': epoch, 'reason': 'root-epoch'}
        ok, value = _recover_root(d, root, epoch)
        if not ok:
            return {'ok': False, 'root_epoch': epoch, 'reason': value}
        return {'ok': True, 'root_epoch': epoch, 'prefix': epoch + 1,
                'objects_hex': {'object': value.encode('ascii').hex()}}
    except Exception as e:
        return {'ok': False, 'root_epoch': epoch, 'reason': type(e).__name__}


def child_main(argv: list[str]) -> int:
    d=pathlib.Path(argv[0]); protocol=argv[1]; after=int(argv[2]) if argv[2]!='none' else None
    update(d,protocol,after); return 0

if __name__=='__main__':
    raise SystemExit(child_main(sys.argv[1:]))
