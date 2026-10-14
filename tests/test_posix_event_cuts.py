"""Deterministic owned-helper cuts, not Linux or physical power-loss tests.

Real temporary writes/flushes/replaces execute on Windows. Directory operations
and fsync are mocked. A test-local retention rule supplies the recovery image:
file fsync seals the bytes, directory fsync retains the replacement, and an
earlier rename either survives or is discarded. No torn bytes, concurrent
helpers, selector rollback, lost completed files or post-recovery writes are
modeled. The two rename outcomes are assumptions, not inferred OS guarantees.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import posix_harness as native
from medium import materialize, recover as model_recover
from model import admit_trace
from selector_bridge import boundary_cut, boundary_trace

STAGES = ('file-written', 'file-flushed', 'file-synced', 'file-closed',
          'replaced', 'dir-opened', 'dir-synced', 'dir-closed', 'helper-complete')
DIR_FD = 987654


class StopAtCut(BaseException):
    """Stop only this local test; never terminate a process."""


@contextmanager
def mocked_sync():
    # Keep Python file I/O real; never ask Windows to fsync a directory.
    with patch.object(native.os, 'open', return_value=DIR_FD) as opened, \
         patch.object(native.os, 'fsync') as synced, \
         patch.object(native.os, 'close') as closed, \
         patch.dict(native.os.environ):
        native.os.environ.pop('RF_BOUNDARY_LOG', None)
        yield opened, synced, closed


class RetainedFiles:
    """Small test-local snapshot rule, with no recovery/specification oracle."""

    def __init__(self, directory, protocol, keep_rename, initialized=True):
        self.keep_rename = keep_rename
        self.files = {p.name: p.read_bytes() for p in directory.iterdir()}
        self.cut = set(range(3)) if initialized else set()
        self.sealed = None
        self.pending = None
        if initialized:
            self.event_ids = {'slot-1.json': (3,),
                              'slot-0.json': (6,) if protocol == 'ordered' else (3,),
                              'root-1.json': (4, 5), 'CURRENT': ()}
        else:
            self.event_ids = {'slot-0.json': (0,), 'root-0.json': (1, 2), 'CURRENT': ()}

    def boundary(self, path, stage):
        if stage == 'file-synced':
            self.sealed = path.with_name(path.name + '.tmp').read_bytes()
        elif stage == 'replaced':
            if self.sealed is None:
                raise RuntimeError('replacement before file sync')
            self.pending = (path.name, self.sealed)
            self.sealed = None
        elif stage == 'dir-synced':
            if self.pending is None:
                raise RuntimeError('directory sync before replacement')
            name, data = self.pending
            self.files[name] = data
            self.cut.update(self.event_ids[name])
            self.pending = None

    def snapshot(self, directory):
        files, cut = dict(self.files), set(self.cut)
        if self.keep_rename and self.pending is not None:
            name, data = self.pending
            files[name] = data
            cut.update(self.event_ids[name])
        for name, data in files.items():
            (directory / name).write_bytes(data)
        return sorted(cut)


class PosixEventCutTests(unittest.TestCase):
    def compare(self, directory, model, cut):
        self.assertTrue(model.is_legal_cut(cut))
        expected = model_recover(materialize(model, cut))
        actual = native.recover_latest(directory)
        self.assertEqual(actual['ok'], expected['ok'], (cut, actual, expected))
        selected = expected.get('root_event')
        self.assertEqual(actual['root_epoch'], None if selected is None else (0 if selected == 2 else 1))
        if expected['ok']:
            self.assertEqual(actual['prefix'], expected['prefix'])
            self.assertEqual(actual['objects_hex'], expected['objects_hex'])
            # Recovery is read-only and repeatable on this supplied image.
            self.assertEqual(actual, native.recover_latest(directory))
        return actual

    def test_successful_helper_reports_actual_io_order(self):
        with tempfile.TemporaryDirectory() as td, mocked_sync() as (opened, synced, closed):
            directory = Path(td)
            target = directory / 'record.json'
            seen = []

            def observe(path, stage):
                self.assertEqual(path, target)
                seen.append(stage)
                if stage == 'file-synced':
                    self.assertEqual(synced.call_count, 1)
                    self.assertFalse(target.exists())
                if stage == 'replaced':
                    self.assertEqual(native.read_json(target), {'value': 7})
                    self.assertFalse(target.with_name('record.json.tmp').exists())
                    opened.assert_not_called()
                if stage == 'dir-synced':
                    self.assertEqual(synced.call_count, 2)
                    self.assertEqual(synced.call_args.args, (DIR_FD,))
                    closed.assert_not_called()
                if stage == 'dir-closed':
                    closed.assert_called_once_with(DIR_FD)

            native.atomic_json(target, {'value': 7}, event_hook=observe)
            self.assertEqual(tuple(seen), STAGES)
            opened.assert_called_once_with(directory, native.os.O_RDONLY | getattr(native.os, 'O_DIRECTORY', 0))

    def test_every_initialization_event_cut_against_existing_model(self):
        model = admit_trace(boundary_trace('ordered'))
        comparisons = 0
        for keep in (False, True):
            for after in range(28):  # empty state plus 3 helpers x 9 stages
                with self.subTest(keep_pending_rename=keep, event_cut=after), \
                     tempfile.TemporaryDirectory() as td:
                    directory = Path(td)
                    live, retained = directory / 'live', directory / 'retained'
                    live.mkdir(); retained.mkdir()
                    view = RetainedFiles(live, 'ordered', keep, initialized=False)
                    seen = []

                    def stop(path, stage):
                        view.boundary(path, stage)
                        seen.append((path.name, stage))
                        if len(seen) == after:
                            raise StopAtCut()

                    with mocked_sync():
                        if after:
                            with self.assertRaises(StopAtCut):
                                native.init_store(live, event_hook=stop)
                    expected = [(name, stage) for name in ('slot-0.json', 'root-0.json', 'CURRENT')
                                for stage in STAGES]
                    self.assertEqual(seen, expected[:after])
                    self.compare(retained, model, view.snapshot(retained))
                    comparisons += 1
        self.assertEqual(comparisons, 56)

    def test_every_update_event_cut_against_existing_model(self):
        comparisons, failures = 0, {'ordered': 0, 'unsafe': 0}
        for protocol in ('ordered', 'unsafe'):
            model = admit_trace(boundary_trace(protocol))
            names = (('slot-1.json', 'root-1.json', 'CURRENT', 'slot-0.json')
                     if protocol == 'ordered' else ('slot-0.json', 'root-1.json', 'CURRENT'))
            expected = [(name, stage) for name in names for stage in STAGES]
            for keep in (False, True):
                for after in range(len(expected) + 1):
                    with self.subTest(protocol=protocol, keep_pending_rename=keep, event_cut=after), \
                         tempfile.TemporaryDirectory() as td:
                        live, retained = Path(td) / 'live', Path(td) / 'retained'
                        retained.mkdir()
                        with mocked_sync():
                            native.init_store(live)
                        view = RetainedFiles(live, protocol, keep)
                        seen = []

                        def stop(path, stage):
                            view.boundary(path, stage)
                            seen.append((path.name, stage))
                            if len(seen) == after:
                                raise StopAtCut()

                        with mocked_sync():
                            if after:
                                with self.assertRaises(StopAtCut):
                                    native.update(live, protocol, None, event_hook=stop)
                        self.assertEqual(seen, expected[:after])
                        actual = self.compare(retained, model, view.snapshot(retained))
                        comparisons += 1
                        failures[protocol] += not actual['ok']
        self.assertEqual(comparisons, 130)
        self.assertEqual(failures, {'ordered': 0, 'unsafe': 18})

    def test_original_completed_selector_cuts_still_agree(self):
        comparisons = 0
        for protocol in ('ordered', 'unsafe'):
            model = admit_trace(boundary_trace(protocol))
            count = 4 if protocol == 'ordered' else 3
            for after in range(count + 2):
                with self.subTest(protocol=protocol, after=after), tempfile.TemporaryDirectory() as td:
                    directory = Path(td)
                    with mocked_sync(), patch.object(native.os, '_exit', side_effect=StopAtCut) as exited:
                        native.init_store(directory)
                        if 0 < after <= count:
                            with self.assertRaises(StopAtCut):
                                native.update(directory, protocol, after)
                            exited.assert_called_once_with(native.CRASH_EXIT)
                        elif after > count:
                            self.assertEqual(native.update(directory, protocol, after), count)
                            exited.assert_not_called()
                    expected = model_recover(materialize(model, boundary_cut(protocol, after)))
                    ok, payload = native.recover(directory)
                    self.assertEqual(ok, expected['ok'])
                    if ok:
                        self.assertEqual({'object': payload.encode('ascii').hex()}, expected['objects_hex'])
                    comparisons += 1
        self.assertEqual(comparisons, 11)

    def test_retained_root_before_current_is_a_nontrivial_policy_difference(self):
        for protocol in ('ordered', 'unsafe'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as td:
                live, retained = Path(td) / 'live', Path(td) / 'retained'
                retained.mkdir()
                with mocked_sync():
                    native.init_store(live)
                view = RetainedFiles(live, protocol, False)

                def stop(path, stage):
                    view.boundary(path, stage)
                    if path.name == 'root-1.json' and stage == 'dir-synced':
                        raise StopAtCut()

                with mocked_sync(), self.assertRaises(StopAtCut):
                    native.update(live, protocol, None, event_hook=stop)
                cut = view.snapshot(retained)
                self.assertEqual(cut, list(range(6)))
                actual = self.compare(retained, admit_trace(boundary_trace(protocol)), cut)
                self.assertEqual(actual['objects_hex'], {'object': (b'B' * 32).hex()})
                self.assertEqual(native.recover(retained), (True, 'A' * 32) if protocol == 'ordered'
                                 else (False, 'stale-generation'))
                # No selector is needed by latest-root recovery.
                (retained / 'CURRENT').unlink()
                self.assertEqual(native.recover_latest(retained), actual)

    def test_latest_is_strict_not_fallback_and_ignores_temporary_roots(self):
        with tempfile.TemporaryDirectory() as td, mocked_sync():
            directory = Path(td)
            native.init_store(directory)
            native.atomic_json(directory / 'root-1.json.tmp', native._root(1, 1, 0, b'B' * 32))
            self.assertEqual(native.recover_latest(directory)['root_epoch'], 0)
            native.atomic_json(directory / 'root-1.json', native._root(1, 1, 0, b'B' * 32))
            self.assertEqual(native.recover(directory), (True, 'A' * 32))
            latest = native.recover_latest(directory)
            self.assertFalse(latest['ok'])
            self.assertEqual(latest['root_epoch'], 1)
            self.assertEqual(latest['reason'], 'FileNotFoundError')
            native.atomic_json(directory / 'root-1.json', {'epoch': 1, 'objects': {}})
            self.assertFalse(native.recover_latest(directory)['ok'])
            native.atomic_json(directory / 'root-1.json', native._root(0, 0, 0, b'A' * 32))
            self.assertEqual(native.recover_latest(directory)['reason'], 'root-epoch')

    def test_latest_uses_numeric_epoch_and_validates_selected_bytes(self):
        with tempfile.TemporaryDirectory() as td, mocked_sync():
            directory = Path(td)
            native.init_store(directory)
            for epoch in (9, 10):
                native.atomic_json(directory / f'root-{epoch}.json', native._root(epoch, 0, 0, b'A' * 32))
            self.assertEqual(native.recover_latest(directory)['root_epoch'], 10)
            record = native._slot(0, 0, b'A' * 32)
            record['payload_hex'] = (b'B' * 32).hex()
            native.atomic_json(directory / 'slot-0.json', record)
            self.assertEqual(native.recover_latest(directory)['reason'], 'digest')
            native.atomic_json(directory / 'slot-0.json', native._slot(0, 1, b'A' * 32))
            self.assertEqual(native.recover_latest(directory)['reason'], 'stale-generation')

    def test_io_errors_do_not_report_later_success_boundaries(self):
        # Failing operations are not reported as completed. Directory fds must
        # close even when fsync or a callback stops an in-helper execution.
        for failure, prefix in (('file-sync', STAGES[:2]), ('replace', STAGES[:4]),
                                ('dir-open', STAGES[:5]), ('dir-sync', STAGES[:6]),
                                ('dir-close', STAGES[:7])):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as td, \
                 mocked_sync() as (opened, synced, closed):
                target = Path(td) / 'record.json'
                seen = []
                if failure in ('file-sync', 'dir-sync'):
                    def fail_sync(fd):
                        if (fd == DIR_FD) == (failure == 'dir-sync'):
                            raise OSError('local injected sync failure')
                    synced.side_effect = fail_sync
                if failure == 'dir-open': opened.side_effect = OSError('local open failure')
                if failure == 'dir-close': closed.side_effect = OSError('local close failure')
                replace_error = OSError('local replace failure') if failure == 'replace' else None
                with patch.object(native.os, 'replace', wraps=native.os.replace,
                                  side_effect=replace_error), self.assertRaises(OSError):
                    native.atomic_json(target, {'value': 7}, event_hook=lambda path, stage: seen.append(stage))
                self.assertEqual(tuple(seen), prefix)
                if failure in ('dir-sync', 'dir-close'):
                    closed.assert_called_once_with(DIR_FD)
                else:
                    closed.assert_not_called()

    def test_cut_after_directory_open_closes_fd_without_later_hook(self):
        with tempfile.TemporaryDirectory() as td, mocked_sync() as (_, synced, closed):
            seen = []

            def stop(path, stage):
                seen.append(stage)
                if stage == 'dir-opened': raise StopAtCut()

            with self.assertRaises(StopAtCut):
                native.atomic_json(Path(td) / 'record.json', {}, event_hook=stop)
            self.assertEqual(tuple(seen), STAGES[:6])
            self.assertEqual(synced.call_count, 1)
            closed.assert_called_once_with(DIR_FD)


if __name__ == '__main__':
    unittest.main(verbosity=2)
