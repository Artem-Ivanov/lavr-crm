import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import types
import unittest
from unittest.mock import patch


OPERATOR = Path(__file__).resolve().parents[1] / 'ops/deploy.py'


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(OPERATOR.is_file(), 'persistent Git deploy operator is missing')
        spec = importlib.util.spec_from_file_location('git_deploy', OPERATOR)
        self.deploy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.deploy)
        quiet = patch.object(self.deploy, 'progress')
        quiet.start()
        self.addCleanup(quiet.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.seed, self.root = self.base / 'seed', self.base / 'live'
        self.seed.mkdir()
        self.git(self.seed, 'init', '-b', 'master')
        self.git(self.seed, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.seed, 'config', 'user.name', 'Deploy fixture')
        (self.seed / 'launch.sh').write_text('#!/bin/sh\nexit 0\n')
        (self.seed / 'launch.sh').chmod(0o755)
        self.git(self.seed, 'add', 'launch.sh')
        self.commit('README.md', 'old')
        self.git(self.base, 'clone', str(self.seed), str(self.root))
        self.old = self.git(self.root, 'rev-parse', 'HEAD')
        self.deploy.ORIGIN = str(self.seed)
        self.operator = self.deploy.Deploy(self.root, self.base / 'control', self.base / 'releases')

    def git(self, cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, path, value):
        file = self.seed / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)
        self.git(self.seed, 'add', path)
        self.git(self.seed, 'commit', '-m', 'fixture change')
        return self.git(self.seed, 'rev-parse', 'HEAD')

    def test_rejects_bad_sha_before_git(self):
        for value in ('HEAD', 'abc123', 'a' * 39, 'x' * 40, 'a' * 40 + ';touch /tmp/x'):
            with self.subTest(value=value), self.assertRaisesRegex(self.deploy.DeployError, 'invalid_sha'):
                self.operator.select_commit(value)

    def test_fetches_exact_master_commit_without_changing_live_head(self):
        candidate = self.commit('README.md', 'new')
        self.assertEqual(self.operator.select_commit(candidate), self.old)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.old)
        self.assertEqual(self.git(self.root, 'show', candidate + ':README.md'), 'new')

    def test_rejects_commit_outside_master(self):
        self.git(self.seed, 'checkout', '-b', 'side')
        candidate = self.commit('README.md', 'side')
        self.git(self.root, 'fetch', 'origin', 'side')
        self.git(self.seed, 'checkout', 'master')
        with self.assertRaisesRegex(self.deploy.DeployError, 'not_on_master'):
            self.operator.select_commit(candidate)

    def test_rejects_migrations_and_private_runtime_files(self):
        for path in ('backend/alembic/versions/0009.py', 'backend/app/bootstrap.py',
                     'backend/entrypoint.sh', 'compose.yaml', 'docker/Caddyfile',
                     '.env.production', 'private/notifier-ca.crt', 'compose.notifier.json'):
            with self.subTest(path=path):
                candidate = self.commit(path, 'forbidden')
                with self.assertRaises(self.deploy.DeployError):
                    self.operator.select_commit(candidate)
                self.git(self.seed, 'reset', '--hard', self.old)

    def test_rejects_dirty_live_checkout(self):
        (self.root / 'README.md').write_text('operator edit')
        with self.assertRaisesRegex(self.deploy.DeployError, 'dirty_checkout'):
            self.operator.select_commit(self.old)

    def test_compose_environment_preserved_when_base_image_env_changes(self):
        expected = {'SESSION_SECRET': 'a=b', 'PUBLIC_SITE_URL': 'https://example.invalid'}
        env = ['PATH=/new/python/bin', 'SESSION_SECRET=a=b', 'PUBLIC_SITE_URL=https://example.invalid']
        first = self.deploy.environment_hash(env, expected)
        self.assertEqual(first, self.deploy.environment_hash(['PATH=/old/bin', *env[1:]], expected))
        with self.assertRaisesRegex(self.deploy.DeployError, 'environment_mismatch'):
            self.deploy.environment_hash(['SESSION_SECRET=changed', env[2]], expected)

    def test_maintenance_preserves_hosts_and_original(self):
        original = {'apps': {'http': {'servers': {'srv0': {'routes': [
            {'match': [{'host': ['example.invalid']}], 'handle': [{'handler': 'reverse_proxy'}]}
        ]}}}}}
        saved = copy.deepcopy(original)
        frozen = self.deploy.maintenance_runtime(original)
        self.assertEqual(original, saved)
        route = frozen['apps']['http']['servers']['srv0']['routes'][0]
        self.assertEqual(route['match'], saved['apps']['http']['servers']['srv0']['routes'][0]['match'])
        self.assertEqual(route['handle'][0]['status_code'], 503)

    def test_media_backup_reads_contents_and_rejects_links(self):
        path = self.base / 'media.tar'
        with tarfile.open(path, 'w') as archive:
            item = tarfile.TarInfo('photo.jpg')
            item.size = 3
            archive.addfile(item, io.BytesIO(b'abc'))
        first = self.deploy.media_inventory(path)
        self.assertEqual((first['files'], first['bytes']), (1, 3))
        with tarfile.open(path, 'w') as archive:
            item = tarfile.TarInfo('link')
            item.type = tarfile.SYMTYPE
            item.linkname = '/private/data'
            archive.addfile(item)
        with self.assertRaisesRegex(self.deploy.DeployError, 'invalid_media_archive'):
            self.deploy.media_inventory(path)

    def test_failed_health_restores_exact_images_git_and_private_env(self):
        candidate = self.commit('README.md', 'new')
        self.operator.select_commit(candidate)
        (self.root / '.env.docker').write_text('SESSION_SECRET=untouched\n')
        release = self.base / 'release'
        release.mkdir()
        self.operator.stage = release
        previous = {'backend': 'sha256:old-api', 'catalog-imports': 'sha256:older-worker',
                    'frontend': 'sha256:old-ui', 'notifications': 'sha256:old-notifications'}
        receipt = {'old_commit': self.old, 'commit': candidate, 'old_images': previous,
                   'images': {name: 'sha256:new-' + name for name in previous},
                   'caddy': {}, 'baseline': {}, 'protected': {}, 'verify_data': False}
        events = []
        with patch.object(self.operator, 'set_runtime', side_effect=lambda value: events.append(('caddy', value))), \
             patch.object(self.deploy, 'maintenance_runtime', return_value={'maintenance': True}), \
             patch.object(self.operator, 'database_guard'), patch.object(self.operator, 'backup'), \
             patch.object(self.operator, 'stop', side_effect=lambda images: events.append(('stop', images))), \
             patch.object(self.operator, 'start', side_effect=lambda images: events.append(('start', images))), \
             patch.object(self.operator, 'verify', side_effect=[self.deploy.DeployError('health_timeout'), {}]), \
             patch.object(self.operator, 'public_smoke'):
            with self.assertRaisesRegex(self.deploy.DeployError, 'health_timeout'):
                self.operator.apply(receipt)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.old)
        self.assertEqual((self.root / '.env.docker').read_text(), 'SESSION_SECRET=untouched\n')
        self.assertEqual([event[1] for event in events if event[0] == 'start'][-1], previous)
        rollback_start = events.index(('start', previous))
        self.assertIn(('caddy', {'maintenance': True}), events[2:rollback_start])
        self.assertEqual(json.loads((release / 'receipt.json').read_text())['status'], 'rolled_back')

    def test_rollback_restarts_distinct_worker_image_before_api_alias_changes(self):
        images = {'backend': 'sha256:api', 'catalog-imports': 'sha256:worker',
                  'frontend': 'sha256:ui', 'notifications': 'sha256:notifications'}
        calls = []
        with patch.object(self.operator, 'run', side_effect=lambda args, **kw: calls.append(args) or ''):
            self.operator.start(images)
        ups = [args for args in calls if 'up' in args]
        self.assertEqual(len(ups), 3)
        self.assertEqual(ups[0][-1:], ['catalog-imports'])
        self.assertEqual(ups[1][-1:], ['notifications'])
        self.assertEqual(ups[2][-2:], ['backend', 'frontend'])
        self.assertLess(calls.index(['docker', 'tag', 'sha256:notifications', 'lavr-crm-backend:local']),
                        calls.index(ups[1]))
        self.assertLess(calls.index(ups[1]),
                        calls.index(['docker', 'tag', 'sha256:api', 'lavr-crm-backend:local']))
        for args in ups:
            for flag in ('--no-deps', '--no-build', '--pull', 'never'):
                self.assertIn(flag, args)

    def test_start_recreates_notifications_only_when_present_in_images(self):
        for active in (False, True):
            with self.subTest(active=active):
                images = {'backend': 'sha256:api', 'catalog-imports': 'sha256:api', 'frontend': 'sha256:ui'}
                if active:
                    images['notifications'] = 'sha256:api'
                calls = []
                with patch.object(self.operator, 'run', side_effect=lambda args, **kw: calls.append(args) or ''):
                    self.operator.start(images)
                ups = [args for args in calls if 'up' in args]
                expected = ['backend', 'catalog-imports', 'frontend'] + (['notifications'] if active else [])
                self.assertEqual(len(ups), 1)
                self.assertEqual(ups[0][-len(expected):], expected)

    def test_worker_short_stop_requires_idle_imports_and_keeps_http_drain(self):
        calls = []
        with patch.object(self.operator, 'database_guard', side_effect=self.deploy.DeployError('imports_busy')), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kwargs: calls.append((args, kwargs))):
            with self.assertRaisesRegex(self.deploy.DeployError, 'imports_busy'):
                self.operator.stop({})
        self.assertEqual(calls, [])
        with patch.object(self.operator, 'database_guard'), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kwargs: calls.append((args, kwargs))):
            self.operator.stop({})
        self.assertEqual(calls[0][0][-4:], ['stop', '-t', '10', 'catalog-imports'])
        self.assertEqual(calls[0][1]['timeout'], 30)
        self.assertEqual(calls[1][0][-5:], ['stop', '-t', '150', 'frontend', 'backend'])

    def test_stop_includes_active_notifications_before_http_drain(self):
        calls = []
        with patch.object(self.operator, 'database_guard'), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kwargs: calls.append(args)):
            self.operator.stop({'notifications': 'sha256:api'})
        self.assertEqual(calls[0][-5:], ['stop', '-t', '10', 'catalog-imports', 'notifications'])
        self.assertEqual(calls[1][-5:], ['stop', '-t', '150', 'frontend', 'backend'])

    def test_candidate_probe_protects_env_and_removes_it_after_failure(self):
        self.operator.config = {'services': {'backend': {'environment': {'SESSION_SECRET': 'private=value'}}}}
        def failed_probe(args, **kwargs):
            path = Path(args[args.index('--env-file') + 1])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), 'SESSION_SECRET=private=value\n')
            self.assertNotIn('private=value', ' '.join(args))
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertEqual(args[args.index('--user') + 1], '1000:1000')
            self.assertIn('--read-only', args)
            raise self.deploy.DeployError('candidate_import_failed')
        with patch.object(self.operator, 'run', side_effect=failed_probe):
            with self.assertRaisesRegex(self.deploy.DeployError, 'candidate_import_failed'):
                self.operator.probe_candidate('sha256:candidate')
        self.assertFalse((self.operator.stage / 'candidate.env').exists())

    def test_candidate_probe_rejects_missing_manual_order_and_orders_only_contracts(self):
        self.operator.config = {'services': {'backend': {'environment': {}}}}
        for manual_orders, orders_only in ((False, True), (True, False), (True, True)):
            with self.subTest(manual_orders=manual_orders, orders_only=orders_only):
                modules = {name: types.ModuleType(name) for name in
                           ('app', 'app.main', 'app.orders', 'app.notification_worker')}
                modules['app.main'].create_app = lambda: None
                class StatusInput:
                    def __init__(self, *, status):
                        if status == 'confirmed' and not manual_orders:
                            raise ValueError('manual_order_status_unsupported')
                modules['app.orders'].StatusInput = StatusInput
                def unexpected_queue_call(*args, **kwargs):
                    self.fail('candidate probe must not execute the notification queue')
                modules['app.notification_worker'].process_once = (
                    lambda *, orders_only=False: unexpected_queue_call()) if orders_only else unexpected_queue_call
                def candidate(args, **kwargs):
                    with patch.dict(sys.modules, modules):
                        exec(args[-1], {})
                    return ''
                with patch.object(self.operator, 'run', side_effect=candidate):
                    if manual_orders and orders_only:
                        self.operator.probe_candidate('sha256:candidate')
                    else:
                        with self.assertRaises((ValueError, RuntimeError)):
                            self.operator.probe_candidate('sha256:candidate')
                self.assertFalse((self.operator.stage / 'candidate.env').exists())

    def test_notification_snapshot_requires_exact_explicit_compose_command(self):
        expected = ['python', '-m', 'app.notification_worker', '--orders-only']
        item = {'Id': 'worker', 'Image': 'sha256:api', 'RestartCount': 0, 'Mounts': [],
                'State': {'StartedAt': 'now', 'Running': True},
                'Config': {'Labels': {'com.docker.compose.service': 'notifications'}, 'Env': []}}
        for configured, actual, accepted in ((expected, expected, True),
                                             (expected, expected[:-1], False),
                                             (expected, None, False),
                                             (None, None, False),
                                             ('python -m app.notification_worker --orders-only',
                                              'python -m app.notification_worker --orders-only', False)):
            with self.subTest(configured=configured, actual=actual):
                self.operator.config = {'services': {'notifications': {'command': configured}}}
                item['Config']['Cmd'] = actual
                with patch.object(self.operator, 'run', side_effect=['worker', json.dumps([item])]):
                    if accepted:
                        self.assertEqual(self.operator.snapshot()['notifications']['id'], 'worker')
                    else:
                        with self.assertRaisesRegex(self.deploy.DeployError, 'notification_command_mismatch'):
                            self.operator.snapshot()

    def baseline(self):
        return {name: {'id': name, 'image': 'sha256:old-' + name, 'started': 'old', 'restarts': 0,
                       'running': True, 'oom': False, 'health': None if name in ('catalog-imports', 'https') else 'healthy',
                       'env': 'unchanged', 'mounts': []}
                for name in ('backend', 'catalog-imports', 'frontend', 'postgres', 'https')}

    def notification_baseline(self):
        baseline = self.baseline()
        baseline['notifications'] = {**baseline['catalog-imports'], 'id': 'notifications',
                                     'image': 'sha256:old-notifications'}
        return baseline

    def test_verify_tracks_notification_image_and_configuration_without_healthcheck(self):
        baseline = self.notification_baseline()
        images = {'backend': 'sha256:api', 'catalog-imports': 'sha256:api',
                  'frontend': 'sha256:ui', 'notifications': 'sha256:api'}
        current = copy.deepcopy(baseline)
        for name, image in images.items():
            current[name].update(id='new-' + name, image=image)
        with patch.object(self.operator, 'snapshot', return_value=current), \
             patch.object(self.operator, 'protected', return_value={}):
            self.assertEqual(self.operator.verify(images, baseline, {}), current)
            for field, value, reason in (('image', 'sha256:wrong', 'target_image_or_oom'),
                                         ('oom', True, 'target_image_or_oom'),
                                         ('env', 'changed', 'service_configuration_changed'),
                                         ('mounts', [{'Destination': '/new'}], 'service_configuration_changed'),
                                         ('restarts', 1, 'target_restarted'),
                                         ('running', False, 'health_timeout')):
                with self.subTest(field=field):
                    saved = current['notifications'][field]
                    current['notifications'][field] = value
                    with patch.object(self.deploy.time, 'monotonic', side_effect=[0, 181]):
                        with self.assertRaisesRegex(self.deploy.DeployError, reason):
                            self.operator.verify(images, baseline, {})
                    current['notifications'][field] = saved

    def test_verify_rejects_notification_baseline_without_saved_image(self):
        baseline = self.notification_baseline()
        images = {name: baseline[name]['image'] for name in ('backend', 'catalog-imports', 'frontend')}
        with patch.object(self.operator, 'snapshot', return_value=baseline), \
             patch.object(self.operator, 'protected', return_value={}):
            with self.assertRaisesRegex(self.deploy.DeployError, 'target_images_mismatch'):
                self.operator.verify(images, baseline, {})

    def test_stopped_notification_baseline_blocks_deploy_before_build(self):
        baseline = self.notification_baseline()
        baseline['notifications']['running'] = False
        with patch.object(self.operator, 'select_commit', return_value=self.old), \
             patch.object(self.operator, 'run', return_value='{"services": {}}'), \
             patch.object(self.operator, 'snapshot', return_value=baseline), \
             patch.object(self.operator, 'protected', return_value={}), \
             patch.object(self.operator, 'database_guard', side_effect=AssertionError('stopped worker reached database guard')):
            with self.assertRaisesRegex(self.deploy.DeployError, 'required_service_not_running'):
                self.operator.deploy(self.old)

    def test_deploy_uses_backend_build_and_saves_old_notification_image(self):
        baseline, receipts, builds = self.notification_baseline(), [], []
        def runtime(args, **kwargs):
            if 'config' in args:
                return '{"services": {}}'
            if args[:2] == ['docker', 'build']:
                builds.append(args)
                return ''
            if args[:3] == ['docker', 'image', 'inspect']:
                return '[{"Id":"sha256:candidate", "Size":1}]'
            if args[:2] == ['docker', 'exec'] and 'du' in args:
                return '1 /data/media\n'
            raise AssertionError('unexpected external command: ' + repr(args))
        with patch.object(self.operator, 'select_commit', return_value=self.old), \
             patch.object(self.operator, 'git', side_effect=lambda *args: self.old if args == ('rev-parse', 'HEAD') else ''), \
             patch.object(self.operator, 'run', side_effect=runtime), \
             patch.object(self.operator, 'snapshot', return_value=baseline), \
             patch.object(self.operator, 'protected', return_value={}), \
             patch.object(self.operator, 'database_guard', return_value={'bytes': 1}), \
             patch.object(self.operator, 'probe_candidate'), \
             patch.object(self.operator, 'active_runtime', return_value={}), \
             patch.object(self.operator, 'apply', side_effect=receipts.append):
            self.operator.deploy(self.old)
        self.assertEqual(len(builds), 2)
        self.assertEqual(receipts[0]['images'], {'backend': 'sha256:candidate', 'frontend': 'sha256:candidate',
                                               'catalog-imports': 'sha256:candidate', 'notifications': 'sha256:candidate'})
        self.assertEqual(receipts[0]['old_images'].get('notifications'), 'sha256:old-notifications')

    def test_first_same_sha_builds_from_exact_detached_checkout(self):
        baseline, builds, receipts, phases = self.baseline(), [], [], []
        real_run = self.operator.run
        def runtime(args, **kwargs):
            if args[0] == 'git':
                return real_run(args, **kwargs)
            if 'config' in args:
                return '{"services": {"backend": {"environment": {"SESSION_SECRET": "private"}}}}'
            if args[:2] == ['docker', 'build']:
                builds.append(args)
                phases.append('build')
                return ''
            if args[:2] == ['docker', 'run']:
                phases.append('probe')
                return ''
            if args[:3] == ['docker', 'image', 'inspect']:
                return '[{"Id":"sha256:candidate", "Size":1}]'
            if args[0:2] == ['docker', 'exec'] and 'du' in args:
                return '1 /data/media\n'
            raise AssertionError('unexpected external command: ' + repr(args))
        with patch.object(self.operator, 'run', side_effect=runtime), \
             patch.object(self.operator, 'snapshot', return_value=baseline), \
             patch.object(self.operator, 'protected', return_value={}), \
             patch.object(self.operator, 'database_guard', return_value={'bytes': 1}), \
             patch.object(self.operator, 'active_runtime', return_value={}), \
             patch.object(self.operator, 'apply', side_effect=lambda receipt: (phases.append('apply'), receipts.append(receipt))):
            self.operator.deploy(self.old)
        self.assertEqual(len(builds), 2)
        source = self.operator.stage / 'source'
        self.assertEqual(self.git(source, 'rev-parse', 'HEAD'), self.old)
        self.assertEqual(self.git(self.root, 'rev-parse', 'HEAD'), self.old)
        self.assertEqual((source / 'README.md').stat().st_mode & 0o777, 0o644)
        self.assertEqual((source / 'launch.sh').stat().st_mode & 0o777, 0o755)
        self.assertEqual(source.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.operator.stage.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.operator.stage / 'commands.log').stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.umask(0o077), 0o077)
        self.assertEqual(Path(builds[0][-1]), source / 'backend')
        self.assertIn('VITE_DATA_MODE=api', builds[1])
        self.assertTrue(receipts[0]['verify_data'])
        self.assertEqual(phases, ['build', 'probe', 'build', 'apply'])

    def test_successful_same_sha_is_verified_without_rebuild(self):
        baseline = self.baseline()
        images = {name: baseline[name]['image'] for name in self.deploy.TARGETS}
        self.deploy.private_json(self.operator.control / 'state.json',
                                 {'status': 'success', 'current_commit': self.old, 'protected': {},
                                  'images': images, 'receipt': '/private/receipt.json', 'data_verified': True})
        real_run = self.operator.run
        def runtime(args, **kwargs):
            if args[0] == 'git':
                return real_run(args, **kwargs)
            if 'config' in args:
                return '{"services": {}}'
            raise AssertionError('a repeated SHA must not build or recreate containers')
        with patch.object(self.operator, 'run', side_effect=runtime), \
             patch.object(self.operator, 'snapshot', return_value=baseline), \
             patch.object(self.operator, 'protected', return_value={}), \
             patch.object(self.operator, 'database_guard'), patch.object(self.operator, 'public_smoke'):
            self.assertEqual(self.operator.deploy(self.old)['status'], 'unchanged')


if __name__ == '__main__':
    unittest.main()
