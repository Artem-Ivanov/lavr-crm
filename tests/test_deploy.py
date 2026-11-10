import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
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
                    'frontend': 'sha256:old-ui'}
        receipt = {'old_commit': self.old, 'commit': candidate, 'old_images': previous,
                   'images': {name: 'sha256:new-' + name for name in previous},
                   'caddy': {}, 'baseline': {}, 'protected': {}, 'verify_data': False}
        events = []
        with patch.object(self.operator, 'set_runtime', side_effect=lambda value: events.append(('caddy', value))), \
             patch.object(self.deploy, 'maintenance_runtime', return_value={'maintenance': True}), \
             patch.object(self.operator, 'database_guard'), patch.object(self.operator, 'backup'), \
             patch.object(self.operator, 'stop', side_effect=lambda: events.append(('stop',))), \
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
        images = {'backend': 'sha256:api', 'catalog-imports': 'sha256:worker', 'frontend': 'sha256:ui'}
        calls = []
        with patch.object(self.operator, 'run', side_effect=lambda args, **kw: calls.append(args) or ''):
            self.operator.start(images)
        ups = [args for args in calls if 'up' in args]
        self.assertEqual(len(ups), 2)
        self.assertEqual(ups[0][-1:], ['catalog-imports'])
        self.assertEqual(ups[1][-2:], ['backend', 'frontend'])
        for args in ups:
            for flag in ('--no-deps', '--no-build', '--pull', 'never'):
                self.assertIn(flag, args)

    def test_worker_short_stop_requires_idle_imports_and_keeps_http_drain(self):
        calls = []
        with patch.object(self.operator, 'database_guard', side_effect=self.deploy.DeployError('imports_busy')), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kwargs: calls.append((args, kwargs))):
            with self.assertRaisesRegex(self.deploy.DeployError, 'imports_busy'):
                self.operator.stop()
        self.assertEqual(calls, [])
        with patch.object(self.operator, 'database_guard'), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kwargs: calls.append((args, kwargs))):
            self.operator.stop()
        self.assertEqual(calls[0][0][-4:], ['stop', '-t', '10', 'catalog-imports'])
        self.assertEqual(calls[0][1]['timeout'], 30)
        self.assertEqual(calls[1][0][-5:], ['stop', '-t', '150', 'frontend', 'backend'])

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

    def baseline(self):
        return {name: {'id': name, 'image': 'sha256:old-' + name, 'started': 'old', 'restarts': 0,
                       'running': True, 'oom': False, 'health': None if name in ('catalog-imports', 'https') else 'healthy',
                       'env': 'unchanged', 'mounts': []}
                for name in ('backend', 'catalog-imports', 'frontend', 'postgres', 'https')}

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
