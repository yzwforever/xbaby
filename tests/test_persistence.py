import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from newsstudy import pipeline, delivery
from newsstudy.persistence import GitPersistence, PersistenceError
from newsstudy.settings import DEFAULT, atomic_json
from newsstudy.state import State
from test_core import DAY, raw_news, result, sandbox_temp


class DeliverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = sandbox_temp()
        self.root = Path(self.temp.name)
        self.config = {**DEFAULT, 'push_enabled': True}
        self.keys = {'serverchan_sendkey': 'SCTfirst,SCTsecond'}
        self.state = State(self.root)
        folder = self.root / 'output/pdf' / DAY
        folder.mkdir(parents=True)
        (folder / 'report.pdf').write_bytes(b'%PDF-fake')
        atomic_json(folder / 'analysis.json', result())
        atomic_json(folder / 'source.json', raw_news())
        self.state.put(DAY, status='uploaded', pdf=str(folder / 'report.pdf'), url='https://example.test/report')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def push(self, persist=None):
        return pipeline.push_pending(self.state, self.config, self.keys, lambda s: None, persist)

    def restore(self, snapshot):
        atomic_json(self.root / 'data/delivery-state.json', snapshot)
        self.state.restore_public()
        # Private signed URLs are regenerated in a real run; the test uses a fake link.
        self.state.put(DAY, url='https://example.test/report')

    def test_export_never_contains_signed_urls_keys_errors_or_paths(self):
        marker = 'AKID' + 'x' * 32
        self.state.put(DAY, url='https://cos.test/report?q-sign-ak=' + marker, error=marker)
        self.state.mark_recipient(DAY, self.state.recipient_id('SCTfirst'), 'uncertain')
        self.state.export_public()
        text = (self.root / 'data/delivery-state.json').read_text()
        for private in (marker, 'SCTfirst', 'q-sign', str(self.root), 'expires', 'error', 'url'):
            self.assertNotIn(private, text)
        self.restore(json.loads(text))
        self.assertEqual(self.state.get(DAY)['notified'], 2)
        self.assertEqual(self.state.recipient_status(DAY, self.state.recipient_id('SCTfirst')), 'uncertain')

    def test_invalid_snapshot_fails_without_replacing_local_state(self):
        atomic_json(self.root / 'data/delivery-state.json', {'version': 1, 'days': [], 'recipients': [], 'url': 'private'})
        with self.assertRaises(ValueError):
            self.state.restore_public()
        self.assertIsNotNone(self.state.get(DAY))

    def test_first_barrier_failure_sends_nothing(self):
        with patch('newsstudy.delivery.send_wechat') as send:
            with self.assertRaises(PersistenceError):
                self.push(lambda state: (_ for _ in ()).throw(PersistenceError('offline')))
            send.assert_not_called()

    def test_missing_download_url_never_sends(self):
        self.state.put(DAY, url='')
        with patch('newsstudy.delivery.send_wechat') as send:
            self.assertFalse(self.push())
            send.assert_not_called()

    def test_old_uncertain_dates_do_not_starve_new_date(self):
        for day in ('2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'):
            self.state.put(day, status='uploaded', notified=2)
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            self.assertTrue(self.push())  # A historical hold is not a new failure.
            self.assertEqual(send.call_count, 2)
        self.assertEqual(self.state.get(DAY)['notified'], 1)

    def test_snapshot_without_database_uses_checkout_relative_report_path(self):
        self.state.export_public()
        other_root = self.root / 'relocated'
        other_root.mkdir()
        atomic_json(other_root / 'data/delivery-state.json',
                    json.loads((self.root / 'data/delivery-state.json').read_text()))
        other = State(other_root)
        try:
            self.assertIsNotNone(other.get(DAY))  # Automatic restore also protects GUI.
            other.restore_public()
            self.assertEqual(Path(other.get(DAY)['pdf']), other_root / 'output/pdf' / DAY / 'report.pdf')
            self.assertEqual(other.get(DAY)['url'], '')
        finally:
            other.close()

    def test_invalid_snapshot_blocks_each_fresh_database_open(self):
        other_root = self.root / 'invalid-checkout'
        atomic_json(other_root / 'data/delivery-state.json', {'version': 999})
        for _ in range(2):
            with self.assertRaises(ValueError):
                State(other_root)

    def test_remote_uncertain_exists_before_each_send(self):
        saved = []
        def persist(state):
            state.export_public()
            saved.append(json.loads((self.root / 'data/delivery-state.json').read_text()))
        def send(title, body, key):
            rid = self.state.recipient_id(key)
            self.assertIn({'day': DAY, 'recipient': rid, 'status': 'uncertain'}, saved[-1]['recipients'])
        with patch('newsstudy.delivery.send_wechat', side_effect=send) as sender:
            self.assertTrue(self.push(persist))
            self.push(persist)
            self.assertEqual(sender.call_count, 2)

    def test_post_acceptance_save_failure_does_not_resend_after_restore(self):
        saved = []
        def persist(state):
            if saved:
                raise PersistenceError('post-send offline')
            state.export_public()
            saved.append(json.loads((self.root / 'data/delivery-state.json').read_text()))
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            with self.assertRaises(PersistenceError):
                self.push(persist)
            self.assertEqual(send.call_count, 1)
            self.restore(saved[0])
            self.push()
            self.assertEqual([c.args[2] for c in send.call_args_list], ['SCTfirst', 'SCTsecond'])
            self.assertEqual(self.state.get(DAY)['notified'], 2)

    def test_process_death_after_acceptance_keeps_remote_uncertain(self):
        saved = []
        def persist(state):
            state.export_public()
            saved.append(json.loads((self.root / 'data/delivery-state.json').read_text()))
        with patch('newsstudy.delivery.send_wechat', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.push(persist)
        self.restore(saved[-1])
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            self.push()
            self.assertEqual([c.args[2] for c in send.call_args_list], ['SCTsecond'])

    def test_partial_delivery_retry_only_retries_approved_recipient(self):
        with patch('newsstudy.delivery.send_wechat', side_effect=[True, TimeoutError]) as send:
            self.assertFalse(self.push())
            self.assertTrue(self.push())  # No new attempt: leave the hold, return success.
            self.assertEqual(send.call_count, 2)
        rid = self.state.recipient_id('SCTsecond')
        pipeline.resolve_notifications(self.root, False, DAY, rid)
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            self.assertTrue(self.push())
            self.assertEqual([c.args[2] for c in send.call_args_list], ['SCTsecond'])

    def test_acknowledge_uncertain_does_not_send_again(self):
        with patch('newsstudy.delivery.send_wechat', side_effect=TimeoutError):
            self.push()
        pipeline.resolve_notifications(self.root, True, DAY)
        with patch('newsstudy.delivery.send_wechat') as send:
            self.push()
            send.assert_not_called()

    def test_duplicate_sendkeys_are_sent_once(self):
        self.keys['serverchan_sendkey'] = 'SCTfirst,SCTfirst'
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            self.push()
            self.assertEqual(send.call_count, 1)

    def test_legacy_missing_snapshot_requires_manual_resolution(self):
        self.state.restore_public()
        with patch('newsstudy.delivery.send_wechat') as send:
            self.assertTrue(self.push())
            send.assert_not_called()
        self.assertEqual(self.state.get(DAY)['notified'], 2)

    def test_historical_hold_run_succeeds_without_reupload_or_send(self):
        atomic_json(self.root / 'config.json', {
            **DEFAULT, 'start_date': DAY, 'model_base_url': 'https://fake.test/v1',
            'model_name': 'fake', 'upload_enabled': True, 'push_enabled': True})
        self.state.put(DAY, notified=2, url='')
        with patch('newsstudy.pipeline.load_secrets', return_value={
                    **self.keys, 'model_api_key': 'fake'}), \
             patch('newsstudy.pipeline.collect') as collect, \
             patch('newsstudy.delivery.upload_pdf') as upload, \
             patch('newsstudy.delivery.send_wechat') as send:
            self.assertTrue(pipeline.run(self.root, DAY, lambda s: None))
            collect.assert_not_called()
            upload.assert_not_called()
            send.assert_not_called()
        self.assertEqual(self.state.get(DAY)['notified'], 2)

    def test_existing_recipient_holds_succeed_without_sending(self):
        for key in ('SCTfirst', 'SCTsecond'):
            self.state.mark_recipient(DAY, self.state.recipient_id(key), 'uncertain')
        with patch('newsstudy.delivery.send_wechat') as send:
            self.assertTrue(self.push())
            send.assert_not_called()
        self.assertEqual(self.state.get(DAY)['notified'], 2)

    def test_partial_crash_still_sends_previously_unattempted_recipient(self):
        self.state.mark_recipient(DAY, self.state.recipient_id('SCTfirst'), 'uncertain')
        self.assertTrue(pipeline.has_sendable_recipient(self.state, self.state.get(DAY),
                                                      ['SCTfirst', 'SCTsecond']))
        with patch('newsstudy.delivery.send_wechat', return_value=True) as send:
            self.assertTrue(self.push())
            self.assertEqual([c.args[2] for c in send.call_args_list], ['SCTsecond'])
        self.assertEqual(self.state.get(DAY)['notified'], 2)

    def test_resolution_uses_same_single_instance_lock(self):
        with pipeline.single_instance(self.root):
            with self.assertRaises(RuntimeError):
                pipeline.resolve_notifications(self.root, False, DAY)

    def test_windows_gui_import_still_available(self):
        import newsstudy.ui
        self.assertTrue(callable(newsstudy.ui.launch))


class GitSafetyTests(unittest.TestCase):
    def git(self, *args, cwd=None, check=True):
        proc = subprocess.run(['git', *args], cwd=cwd or self.root, capture_output=True,
                              env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1'}, timeout=20)
        if check and proc.returncode:
            self.fail(proc.stderr.decode(errors='replace'))
        return proc

    def setUp(self):
        self.temp = sandbox_temp()
        self.base = Path(self.temp.name)
        self.root = self.base / 'work'
        self.root.mkdir()
        self.remote = self.base / 'remote.git'
        self.git('init', '--bare', '--initial-branch=main', str(self.remote))
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / 'README.md').write_text('test')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'baseline')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.state = State(self.root)
        self.sync = GitPersistence(self.root)

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_round_trip_excludes_sqlite_and_preserves_recipient_state(self):
        self.state.put(DAY, status='uploaded')
        self.git('add', '-f', 'data/state.sqlite3')
        self.git('commit', '-m', 'legacy')
        self.git('push')
        self.sync.prepare(self.state)
        rid = self.state.recipient_id('SCTfake')
        self.state.mark_recipient(DAY, rid, 'sent')
        self.sync(self.state)
        names = self.git('ls-tree', '-r', '--name-only', 'HEAD').stdout.decode()
        self.assertNotIn('state.sqlite3', names)
        self.assertIn('delivery-state.json', names)
        clone = self.base / 'clone'
        self.git('clone', str(self.remote), str(clone))
        other = State(clone)
        try:
            other.restore_public()
            self.assertEqual(other.recipient_status(DAY, rid), 'sent')
        finally:
            other.close()

    def test_rejected_push_fails_closed(self):
        self.sync.prepare(self.state)
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        self.state.put(DAY, status='uploaded')
        with self.assertRaises(PersistenceError):
            self.sync(self.state)
        self.assertNotEqual(self.sync.remote_head(), self.git('rev-parse', 'HEAD').stdout.decode().strip())

    def test_remote_concurrent_commit_is_not_overwritten(self):
        self.sync.prepare(self.state)
        other = self.base / 'other'
        self.git('clone', str(self.remote), str(other))
        self.git('config', 'user.name', 'Other', cwd=other)
        self.git('config', 'user.email', 'other@example.invalid', cwd=other)
        (other / 'README.md').write_text('concurrent')
        self.git('add', 'README.md', cwd=other)
        self.git('commit', '-m', 'concurrent', cwd=other)
        self.git('push', cwd=other)
        expected = self.sync.remote_head()
        with self.assertRaises(PersistenceError):
            self.sync(self.state)
        self.assertEqual(self.sync.remote_head(), expected)

    def test_unrelated_staged_code_is_not_committed(self):
        (self.root / 'README.md').write_text('user work')
        self.git('add', 'README.md')
        with self.assertRaises(PersistenceError):
            self.sync(self.state)

    def test_secret_in_generated_file_stops_publication(self):
        p = self.root / 'data' / DAY / 'source.json'
        p.parent.mkdir(parents=True)
        p.write_text('AKID' + 'x' * 32)
        with self.assertRaises(PersistenceError):
            self.sync(self.state)

    def test_failed_remote_confirmation_is_an_error(self):
        head = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        with patch.object(self.sync, 'remote_head', side_effect=[head, '0' * 40]):
            with self.assertRaises(PersistenceError):
                self.sync(self.state)

    def test_corrupt_snapshot_prevents_prepare(self):
        atomic_json(self.root / 'data/delivery-state.json', {'version': 999})
        with self.assertRaises(ValueError):
            self.sync.prepare(self.state)

    def test_prepare_does_not_accept_old_checkout(self):
        with patch.object(self.sync, 'remote_head', return_value='0' * 40):
            with self.assertRaises(PersistenceError):
                self.sync.prepare(self.state)

    def test_only_allowlisted_artifacts_are_committed(self):
        (self.root / 'data/private.log').write_text('private')
        (self.root / 'data/state.sqlite3-wal').write_text('private')
        (self.root / 'data/run.lock').write_text('lock')
        self.sync(self.state)
        names = self.git('ls-tree', '-r', '--name-only', 'HEAD').stdout.decode()
        self.assertNotIn('private.log', names)
        self.assertNotIn('state.sqlite3', names)
        self.assertNotIn('run.lock', names)

    def test_configured_key_in_generated_file_is_blocked(self):
        p = self.root / 'data' / DAY / 'source.json'
        p.parent.mkdir(parents=True)
        p.write_text('fake-secret-for-test')
        with patch.dict(os.environ, {'NEWSSTUDY_SERVERCHAN_SENDKEY': 'another-fake-key,fake-secret-for-test'}):
            with self.assertRaises(PersistenceError):
                self.sync(self.state)

    def test_pipeline_observes_remote_ledger_before_mock_delivery(self):
        config = {**DEFAULT, 'start_date': DAY, 'model_base_url': 'https://fake.test/v1',
                  'model_name': 'fake', 'upload_enabled': True, 'push_enabled': True}
        atomic_json(self.root / 'config.json', config)
        self.sync.prepare(self.state)
        def render(raw, analyzed, dest, font):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b'%PDF-fake')
        def send(title, body, key):
            remote = json.loads(self.git('--git-dir', str(self.remote),
                                        'show', 'main:data/delivery-state.json').stdout)
            self.assertIn({'day': DAY, 'recipient': State.recipient_id(key), 'status': 'uncertain'},
                          remote['recipients'])
        with patch('newsstudy.pipeline.load_secrets', return_value={
                    'model_api_key': 'fake', 'serverchan_sendkey': 'SCTfake'}), \
             patch('newsstudy.pipeline.collect', return_value=raw_news()), \
             patch('newsstudy.analysis.analyze', return_value=result()) as model, \
             patch('newsstudy.report.render', side_effect=render), \
             patch('newsstudy.delivery.upload_pdf', return_value='https://fake.test/report?q-sign-ak=private'), \
             patch('newsstudy.delivery.send_wechat', side_effect=send) as sender:
            self.assertTrue(pipeline.run(self.root, DAY, lambda s: None, self.sync))
            self.state.restore_public()
            self.assertTrue(pipeline.run(self.root, DAY, lambda s: None, self.sync))
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(model.call_count, 1)


class CommandSafetyTests(unittest.TestCase):
    def test_actions_without_barrier_fails_before_pipeline(self):
        import newsstudy.__main__ as cli
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'NEWSSTUDY_REMOTE_PERSIST': '0'}), \
             patch('sys.argv', ['newsstudy', 'run']), \
             patch('newsstudy.pipeline.run') as run, patch('newsstudy.pipeline.log_line'):
            self.assertEqual(cli.main(), 1)
            run.assert_not_called()

    def test_recovery_requires_explicit_decision(self):
        import newsstudy.__main__ as cli
        with patch('sys.argv', ['newsstudy', 'resolve', '--date', DAY]), \
             patch('newsstudy.pipeline.resolve_notifications') as resolve, \
             patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                cli.main()
            resolve.assert_not_called()


class CredentialErrorTests(unittest.TestCase):
    def test_cos_exception_body_never_reaches_error(self):
        import sys
        from types import SimpleNamespace
        client = SimpleNamespace(upload_file=lambda **kw: (_ for _ in ()).throw(RuntimeError('AKID' + 'x' * 32)))
        sdk = SimpleNamespace(CosConfig=lambda **kw: None, CosS3Client=lambda config: client)
        with patch.dict(sys.modules, {'qcloud_cos': sdk}):
            with self.assertRaises(RuntimeError) as error:
                delivery.upload_pdf('fake.pdf', DAY,
                    {**DEFAULT, 'cos_region': 'fake', 'cos_bucket': 'fake'},
                    {'cos_secret_id': 'fake', 'cos_secret_key': 'fake'})
        self.assertNotIn('AKID', str(error.exception))


if __name__ == '__main__':
    unittest.main()
