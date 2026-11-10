import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog_deploy', ROOT / 'ops/deploy_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogDeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.operator = catalog.CatalogDeploy(self.path, self.path / 'control', self.path / 'releases')
        self.operator.candidate_sha = 'b' * 40
        self.migration = (ROOT / catalog.MIGRATION).read_text()
        self.events = []

    def query(self, revision='0008', busy=0):
        return json.dumps(dict(revision=revision, busy=busy, site=True, integration=True, users=True, bytes=1))

    def test_generic_controller_still_rejects_the_migration(self):
        generic = catalog.base.Deploy(self.path, self.path / 'generic', self.path / 'releases')
        with self.assertRaisesRegex(catalog.base.DeployError, 'non_code_release'):
            generic.validate_changes([catalog.MIGRATION])
        with patch.object(generic, 'sql', return_value=self.query('0008')):
            with self.assertRaisesRegex(catalog.base.DeployError, 'startup_not_noop'):
                generic.database_guard()

    def test_exact_added_migration_allowed(self):
        self.operator.old_commit = 'a' * 40
        with patch.object(self.operator, 'run', return_value=self.migration), \
             patch.object(self.operator, 'git', return_value='A\t' + catalog.MIGRATION):
            self.operator.validate_changes([catalog.MIGRATION, 'src/cart.js'])

    def test_wrong_migration_bytes_or_other_startup_changes_rejected(self):
        self.operator.old_commit = 'a' * 40
        with patch.object(self.operator, 'run', return_value=self.migration + '# changed\n'):
            with self.assertRaisesRegex(catalog.base.DeployError, 'unexpected_migration_hash'):
                self.operator.validate_changes([catalog.MIGRATION])
        for name in ('backend/alembic/versions/0010.py', 'backend/app/bootstrap.py', 'compose.yaml'):
            with self.subTest(name=name), patch.object(self.operator, 'run', return_value=self.migration), \
                 patch.object(self.operator, 'git', return_value='A\t' + catalog.MIGRATION):
                with self.assertRaisesRegex(catalog.base.DeployError, 'non_code_release'):
                    self.operator.validate_changes([catalog.MIGRATION, name])

    def test_existing_migration_edit_or_unrelated_release_rejected(self):
        self.operator.old_commit = 'a' * 40
        with patch.object(self.operator, 'run', return_value=self.migration), \
             patch.object(self.operator, 'git', return_value='M\t' + catalog.MIGRATION):
            with self.assertRaisesRegex(catalog.base.DeployError, 'migration_must_be_added'):
                self.operator.validate_changes([catalog.MIGRATION])
        with patch.object(self.operator, 'run', return_value=self.migration):
            with self.assertRaisesRegex(catalog.base.DeployError, 'not_catalog_transition'):
                self.operator.validate_changes(['src/cart.js'])

    def test_only_old_and_new_revisions_and_idle_imports_accepted(self):
        for revision in ('0008', '0009'):
            with patch.object(self.operator, 'sql', return_value=self.query(revision)):
                self.assertEqual(self.operator.database_guard()['revision'], revision)
        for revision, busy, reason in (('0007', 0, 'startup_not_noop'), ('0009', 1, 'imports_busy')):
            with patch.object(self.operator, 'sql', return_value=self.query(revision, busy)):
                with self.assertRaisesRegex(catalog.base.DeployError, reason):
                    self.operator.database_guard()

    def test_strict_inventory_excludes_only_version_table(self):
        inventory = [{'table': name, 'count': 1, 'hash': name} for name in ('alembic_version', 'products', 'users')]
        with patch.object(catalog.base.Deploy, 'database_inventory', return_value=inventory):
            self.assertEqual(self.operator.database_inventory(), inventory[1:])

    def test_rollback_uses_candidate_to_downgrade_before_old_start(self):
        receipt = dict(schema_before='0008', images={'backend': 'sha256:candidate'}, database_before=[])
        with patch.object(self.operator, 'database_guard', side_effect=[{'revision': '0009'}, {'revision': '0008'}]), \
             patch.object(self.operator, 'database_inventory', return_value=[]), \
             patch.object(self.operator, 'run', side_effect=lambda args, **kw: self.events.append(args) or ''):
            self.operator.rollback_database(receipt)
        command = self.events[0]
        self.assertEqual(command[-5:], ['--entrypoint', 'alembic', 'backend', 'downgrade', '0008'])
        for argument in ('--no-deps', '--rm', '--pull', 'never'):
            self.assertIn(argument, command)
        override = json.loads((self.operator.stage / 'schema-rollback.compose.json').read_text())
        self.assertEqual(override, {'services': {'backend': {'image': 'sha256:candidate'}}})

    def test_failed_downgrade_blocks_old_image_start(self):
        receipt = dict(schema_before='0008', images={'backend': 'sha256:candidate'}, database_before=[])
        with patch.object(self.operator, 'database_guard', return_value={'revision': '0009'}), \
             patch.object(self.operator, 'run', side_effect=catalog.base.DeployError('fractional_stock')):
            with self.assertRaisesRegex(catalog.base.DeployError, 'fractional_stock'):
                self.operator.rollback_database(receipt)

    def test_backup_failure_can_return_to_old_images_without_a_data_baseline(self):
        with patch.object(self.operator, 'database_guard', return_value={'revision': '0008'}), \
             patch.object(self.operator, 'run') as run:
            self.operator.rollback_database({'schema_before': '0008'})
            run.assert_not_called()

    def test_health_failure_downgrades_before_old_images_and_failed_ddl_keeps_maintenance(self):
        for fail_downgrade in (False, True):
            with self.subTest(fail_downgrade=fail_downgrade):
                self.events = []
                state = {'revision': '0008'}
                old_images, images = {'backend': 'old'}, {'backend': 'candidate'}
                receipt = dict(old_commit='a' * 40, commit='b' * 40, old_images=old_images,
                               images=images, caddy={}, baseline={}, protected={})
                def start(selected):
                    self.events.append(('start', selected))
                    if selected == images:
                        state['revision'] = '0009'
                def run(args, **kwargs):
                    self.assertIn('downgrade', args)
                    self.events.append(('downgrade',))
                    if fail_downgrade:
                        raise catalog.base.DeployError('ddl_failed')
                    state['revision'] = '0008'
                    return ''
                with patch.object(self.operator, 'database_guard', side_effect=lambda: dict(state)), \
                     patch.object(self.operator, 'database_inventory', return_value=[]), \
                     patch.object(self.operator, 'set_runtime', side_effect=lambda value: self.events.append(('runtime', value))), \
                     patch.object(catalog.base, 'maintenance_runtime', return_value={'maintenance': True}), \
                     patch.object(self.operator, 'stop'), patch.object(self.operator, 'backup'), \
                     patch.object(self.operator, 'start', side_effect=start), \
                     patch.object(self.operator, 'verify', side_effect=[catalog.base.DeployError('health_timeout'), {}]), \
                     patch.object(self.operator, 'git'), patch.object(self.operator, 'run', side_effect=run), \
                     patch.object(self.operator, 'save', side_effect=lambda r, status: self.events.append(('save', status))), \
                     patch.object(self.operator, 'public_smoke'), patch.object(catalog.base, 'progress'):
                    reason = 'rollback_failed_maintenance_may_remain' if fail_downgrade else 'health_timeout'
                    with self.assertRaisesRegex(catalog.base.DeployError, reason):
                        self.operator.apply(receipt)
                if fail_downgrade:
                    self.assertNotIn(('start', old_images), self.events)
                    self.assertEqual(self.events[-2:], [('runtime', {'maintenance': True}), ('save', 'rollback_failed')])
                else:
                    self.assertLess(self.events.index(('downgrade',)), self.events.index(('start', old_images)))
                    self.assertEqual(self.events[-1], ('save', 'rolled_back'))

    def test_install_saves_known_old_controller_and_is_idempotent(self):
        old = b'known old controller\n'
        new = catalog.BASE_PATH.read_bytes()
        destination = self.operator.control / 'deploy.py'
        destination.write_bytes(old)
        with patch.object(catalog, 'PREVIOUS_CONTROLLER_SHA256', hashlib.sha256(old).hexdigest()):
            self.operator.install_controller()
            self.operator.install_controller()
        self.assertEqual(destination.read_bytes(), new)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual(next(self.operator.control.glob('deploy-0008-*.py')).read_bytes(), old)

    def test_install_refuses_unknown_or_linked_controller(self):
        destination = self.operator.control / 'deploy.py'
        destination.write_text('unreviewed\n')
        with self.assertRaisesRegex(catalog.base.DeployError, 'unexpected_installed_controller'):
            self.operator.install_controller()
        destination.unlink()
        target = self.path / 'target.py'
        target.write_text('linked\n')
        destination.symlink_to(target)
        with self.assertRaisesRegex(catalog.base.DeployError, 'linked_controller'):
            self.operator.install_controller()

    def test_verified_unchanged_retry_finishes_controller_install(self):
        result = {'status': 'unchanged', 'commit': 'b' * 40, 'receipt': 'private'}
        with patch.object(catalog.base.Deploy, 'deploy', return_value=result), \
             patch.object(self.operator, 'database_guard', return_value={'revision': '0009'}), \
             patch.object(self.operator, 'install_controller') as install:
            self.assertEqual(self.operator.deploy('b' * 40), result)
            install.assert_called_once()

    def test_unchanged_old_schema_cannot_install_the_new_controller(self):
        with patch.object(catalog.base.Deploy, 'deploy', return_value={'status': 'unchanged'}), \
             patch.object(self.operator, 'database_guard', return_value={'revision': '0008'}), \
             patch.object(self.operator, 'install_controller') as install:
            with self.assertRaisesRegex(catalog.base.DeployError, 'controller_requires_revision_0009'):
                self.operator.deploy('b' * 40)
            install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
