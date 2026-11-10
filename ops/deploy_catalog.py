#!/usr/bin/env python3
"""One reviewed 0008 -> 0009 transition, before the initial catalog import."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile


BASE_PATH = Path(__file__).with_name('deploy-base-0009.py')
if not BASE_PATH.exists():
    BASE_PATH = Path(__file__).with_name('deploy.py')
spec = importlib.util.spec_from_file_location('catalog_deploy_base', BASE_PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

MIGRATION = 'backend/alembic/versions/0009_fractional_piece_stock.py'
MIGRATION_SHA256 = 'eef6c284f54a2bd69034ccbb8717fadd49554419c5ea34fca985f14f54700472'
PREVIOUS_CONTROLLER_SHA256 = '964d5725ba90ca8900e76ecf493a621ef88a32ef760705f7ed12450f6b9903ed'


class CatalogDeploy(base.Deploy):
    expected_revisions = ('0008', '0009')
    started_candidate = False

    def select_commit(self, sha):
        base.require(re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'invalid_sha')
        self.candidate_sha, self.old_commit = sha, self.git('rev-parse', 'HEAD')
        old = super().select_commit(sha)
        for source, name in ((BASE_PATH, 'ops/deploy.py'), (Path(__file__), 'ops/deploy_catalog.py')):
            base.require(not source.is_symlink(), 'linked_controller_source')
            committed = self.run(['git', 'show', sha + ':' + name]).encode()
            base.require(source.read_bytes() == committed, 'controller_source_mismatch')
        return old

    def validate_changes(self, changed):
        source = self.run(['git', 'show', self.candidate_sha + ':' + MIGRATION]).encode()
        base.require(hashlib.sha256(source).hexdigest() == MIGRATION_SHA256, 'unexpected_migration_hash')
        if MIGRATION in changed:
            status = self.git('diff', '--name-status', self.old_commit, self.candidate_sha, '--', MIGRATION)
            base.require(status == 'A\t' + MIGRATION, 'migration_must_be_added')
        else:
            base.require(self.old_commit == self.candidate_sha, 'not_catalog_transition')
        super().validate_changes([name for name in changed if name != MIGRATION])

    def database_inventory(self):
        return [table for table in super().database_inventory() if table['table'] != 'alembic_version']

    def start(self, images):
        super().start(images)
        self.started_candidate = not getattr(self, 'recovering', False)

    def verify(self, images, baseline, protected):
        current = super().verify(images, baseline, protected)
        if self.started_candidate:
            base.require(self.database_guard()['revision'] == '0009', 'migration_not_applied')
        return current

    def apply(self, receipt):
        self.recovering = False
        receipt.update(verify_data=True, schema_before=self.database_guard()['revision'],
                       migration_sha256=MIGRATION_SHA256)
        super().apply(receipt)

    def rollback_database(self, receipt):
        self.recovering, self.started_candidate = True, False
        revision = self.database_guard()['revision']
        if 'database_before' not in receipt:
            base.require(revision == receipt['schema_before'], 'rollback_revision_without_baseline')
            return
        if receipt['schema_before'] == '0008' and revision == '0009':
            override = self.stage / 'schema-rollback.compose.json'
            base.private_json(override, {'services': {'backend': {'image': receipt['images']['backend']}}})
            # The old image cannot locate 0009; the candidate alone can safely downgrade it.
            self.run(self.compose + ['-f', str(override), 'run', '--rm', '--no-deps', '--pull', 'never',
                                     '--entrypoint', 'alembic', 'backend', 'downgrade', '0008'], timeout=180)
        base.require(self.database_guard()['revision'] == receipt['schema_before'], 'rollback_revision_mismatch')
        base.require(self.database_inventory() == receipt['database_before'], 'rollback_database_content_changed')

    def install_controller(self):
        destination = self.control / 'deploy.py'
        base.require(destination.is_file() and not destination.is_symlink(), 'linked_controller')
        current, candidate = destination.read_bytes(), BASE_PATH.read_bytes()
        if current == candidate:
            return
        old_hash = hashlib.sha256(current).hexdigest()
        base.require(old_hash == PREVIOUS_CONTROLLER_SHA256, 'unexpected_installed_controller')
        backup = self.control / ('deploy-0008-' + old_hash[:12] + '.py')
        if backup.exists():
            base.require(not backup.is_symlink() and backup.read_bytes() == current, 'controller_backup_conflict')
        else:
            with backup.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o700)
                stream.write(current)
                stream.flush()
                os.fsync(stream.fileno())
        descriptor, name = tempfile.mkstemp(prefix='deploy-0009-', suffix='.tmp', dir=self.control)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o700)
                stream.write(candidate)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        directory = os.open(self.control, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def deploy(self, sha):
        result = super().deploy(sha)
        base.require(self.database_guard()['revision'] == '0009', 'controller_requires_revision_0009')
        # An unchanged retry also finishes a previously interrupted controller installation.
        self.install_controller()
        return result


def main():
    base.require(len(sys.argv) == 2, 'usage_deploy_catalog.py_FULL40SHA')
    operator = CatalogDeploy()
    def interrupted(signum, frame):
        raise base.DeployError('interrupted')
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    with (operator.control / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(operator.deploy(sys.argv[1])))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        print(json.dumps({'status': 'failed', 'reason': str(error) if isinstance(error, base.DeployError) else type(error).__name__}))
        sys.exit(1)
