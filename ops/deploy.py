#!/usr/bin/env python3
"""Manual, server-side Git deployment. Installed outside the application checkout."""
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request


ORIGIN = 'https://github.com/Artem-Ivanov/lavr-crm.git'
TARGETS = ('backend', 'catalog-imports', 'frontend')
ALIASES = {'backend': 'lavr-crm-backend:local', 'frontend': 'lavr-crm:api'}
PROTECTED = ('.env.docker', 'compose.yaml', 'compose.https.yaml',
             'compose.notifier.json', 'private/notifier-ca.crt', 'docker/Caddyfile')


class DeployError(RuntimeError):
    pass


def require(condition, label):
    if not condition:
        raise DeployError(label)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def progress(phase):
    print(json.dumps({'phase': phase}), flush=True)


def media_inventory(path):
    files = {}
    with tarfile.open(path, 'r:') as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            require(not name.is_absolute() and '..' not in name.parts
                    and (item.isfile() or item.isdir()), 'invalid_media_archive')
            if item.isfile():
                require(str(name) not in files, 'duplicate_media_file')
                with archive.extractfile(item) as stream:
                    files[str(name)] = {'bytes': item.size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    return {'files': len(files), 'bytes': sum(value['bytes'] for value in files.values()), 'sha256': digest(files)}


def private_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def environment_hash(values, expected):
    actual = dict(value.split('=', 1) for value in values)
    require(all(actual.get(key) == str(value) for key, value in expected.items()), 'environment_mismatch')
    return digest({key: actual[key] for key in expected})


def maintenance_runtime(original):
    value = copy.deepcopy(original)
    servers = value.get('apps', {}).get('http', {}).get('servers', {})
    require(servers, 'missing_caddy_servers')
    for server in servers.values():
        require(server.get('routes'), 'missing_caddy_routes')
        for route in server['routes']:
            route['handle'] = [{'handler': 'static_response', 'status_code': 503,
                                'headers': {'Retry-After': ['60'], 'Content-Type': ['text/plain; charset=utf-8']},
                                'body': 'Обновление сайта. Повторите через минуту.\n'}]
            route['terminal'] = True
    return value


class Deploy:
    def __init__(self, root=Path('/home/deploy/lavr-crm'), control=Path('/home/deploy/lavr-crm-deploy'),
                 releases=Path('/home/deploy/lavr-crm-releases')):
        os.umask(0o077)
        self.root, self.control, self.releases = Path(root), Path(control), Path(releases)
        self.control.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.stage, self.config = self.control, {}
        self.compose = ['docker', 'compose', '-p', 'lavr-crm', '--env-file', '.env.docker',
                        '-f', 'compose.yaml', '-f', 'compose.https.yaml', '-f', 'compose.notifier.json']

    def run(self, args, timeout=60, data=None, output=None):
        with (self.stage / 'commands.log').open('a', encoding='utf-8') as log:
            os.chmod(log.name, 0o600)
            try:
                with output.open('xb') if output else open(os.devnull, 'w') as destination:
                    result = subprocess.run(args, cwd=self.root, input=data, text=True,
                                            stdout=destination if output else subprocess.PIPE,
                                            stderr=subprocess.PIPE, timeout=timeout,
                                            env={**os.environ, 'APP_MODE': 'api'})
                log.write((result.stdout or '') + (result.stderr or ''))
            except subprocess.TimeoutExpired as error:
                log.write('command_timeout: ' + args[0] + '\n')
                raise DeployError('command_timeout_' + args[0]) from error
            require(result.returncode == 0, 'command_failed_' + args[0])
            if output:
                require(output.stat().st_size > 0, 'empty_backup')
            return result.stdout or ''

    def git(self, *args):
        return self.run(['git', *args]).strip()

    def select_commit(self, sha):
        require(re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'invalid_sha')
        require(not self.git('status', '--porcelain', '--untracked-files=no'), 'dirty_checkout')
        require(self.git('remote', 'get-url', 'origin') == ORIGIN, 'unexpected_origin')
        old = self.git('rev-parse', 'HEAD')
        self.git('fetch', '--prune', 'origin', '+refs/heads/master:refs/remotes/origin/master')
        require(self.git('rev-parse', sha + '^{commit}') == sha, 'not_exact_commit')
        reachable = self.git('rev-list', 'origin/master').splitlines()
        require(sha in reachable, 'not_on_master')
        files = self.git('ls-tree', '-r', '--name-only', sha).splitlines()
        require(not any(name == 'compose.notifier.json' or name.startswith('private/')
                        or any(part.startswith('.env') and not part.endswith('.example')
                               for part in Path(name).parts) for name in files), 'private_file_in_git')
        changed = self.git('diff', '--name-only', old, sha).splitlines()
        # shortcut: code-only releases; schema/startup/deployment changes need a separately reviewed operator.
        forbidden = ('backend/alembic/', 'backend/alembic.ini', 'backend/app/bootstrap.py',
                     'backend/entrypoint.sh', 'compose.', 'docker/Caddyfile')
        require(not any(name.startswith(forbidden) for name in changed), 'non_code_release')
        return old

    def protected(self):
        result = {}
        for name in PROTECTED:
            path = self.root / name
            require(path.is_file() and not path.is_symlink(), 'missing_or_linked_configuration')
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def snapshot(self):
        ids = self.run(self.compose + ['ps', '-aq']).split()
        require(ids, 'missing_containers')
        result = {}
        for item in json.loads(self.run(['docker', 'inspect', *ids])):
            name = item['Config']['Labels']['com.docker.compose.service']
            require(name not in result, 'unexpected_replicas')
            state = item['State']
            result[name] = {'id': item['Id'], 'image': item['Image'], 'started': state['StartedAt'],
                            'restarts': item['RestartCount'], 'running': state['Running'],
                            'oom': state.get('OOMKilled', False),
                            'health': state.get('Health', {}).get('Status'),
                            'env': environment_hash(item['Config'].get('Env', []),
                                                    self.config['services'][name].get('environment', {})),
                            'mounts': sorted([{key: mount.get(key) for key in ('Type', 'Name', 'Source', 'Destination', 'RW')}
                                              for mount in item['Mounts']], key=lambda value: value['Destination'])}
        return result

    def verify(self, images, baseline, protected):
        deadline = time.monotonic() + 180
        while True:
            current = self.snapshot()
            require(self.protected() == protected, 'configuration_changed')
            require(set(current) == set(baseline), 'service_set_changed')
            ready = True
            for name, previous in baseline.items():
                item = current[name]
                require(all(item[key] == previous[key] for key in ('env', 'mounts')), 'service_configuration_changed')
                if name in TARGETS:
                    require(item['image'] == images[name] and not item['oom'], 'target_image_or_oom')
                    require(item['id'] == previous['id'] or item['restarts'] == 0, 'target_restarted')
                    ready = ready and item['running'] and (name == 'catalog-imports' or item['health'] == 'healthy')
                else:
                    require(all(item[key] == previous[key] for key in
                                ('id', 'image', 'started', 'restarts', 'running', 'health')), 'untouched_service_changed')
            if ready:
                return current
            require(time.monotonic() < deadline, 'health_timeout')
            time.sleep(2)

    def sql(self, query):
        return self.run(self.compose + ['exec', '-T', 'postgres', 'sh', '-c',
                                       'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], data=query)

    def database_guard(self):
        value = json.loads(self.sql("""BEGIN READ ONLY; SET LOCAL statement_timeout='10s';
SELECT json_build_object('revision',(SELECT version_num FROM alembic_version),
 'site',EXISTS(SELECT 1 FROM site_configuration WHERE id=1),
 'integration',EXISTS(SELECT 1 FROM integration_state WHERE id=1),
 'users',EXISTS(SELECT 1 FROM users),
 'bytes',pg_database_size(current_database()),
 'busy',count(*) FILTER(WHERE status IN ('queued','parsing','preparing'))) FROM catalog_imports;
ROLLBACK;"""))
        require(value['revision'] == '0008' and value['site'] and value['integration'] and value['users'], 'startup_not_noop')
        require(value['busy'] == 0, 'imports_busy')
        return value

    def database_inventory(self):
        tables = json.loads(self.sql("SELECT json_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname='public';"))
        require(tables and all(re.fullmatch('[a-z][a-z0-9_]*', name) for name in tables), 'invalid_tables')
        queries = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout='60s';"]
        for name in tables:
            queries.append("SELECT json_build_object('table','" + name + "','count',count(*),'hash',"
                           "encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex')) FROM "
                           "(SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM public.\"" + name + "\" t) q;")
        return [json.loads(line) for line in self.sql('\n'.join(queries + ['ROLLBACK;'])).splitlines()]

    def active_runtime(self):
        return json.loads(self.run(self.compose + ['exec', '-T', 'https', 'wget', '-qO-', 'http://127.0.0.1:2019/config/']))

    def set_runtime(self, value):
        command = 'set -eu; umask 077; cat > /tmp/lavr-git-deploy.json; caddy validate --config /tmp/lavr-git-deploy.json; caddy reload --config /tmp/lavr-git-deploy.json'
        self.run(self.compose + ['exec', '-T', 'https', 'sh', '-c', command], data=json.dumps(value))
        require(digest(self.active_runtime()) == digest(value), 'caddy_runtime_mismatch')

    def stop(self):
        self.database_guard()
        self.run(self.compose + ['stop', '-t', '10', 'catalog-imports'], timeout=30)
        self.run(self.compose + ['stop', '-t', '150', 'frontend', 'backend'], timeout=180)

    def probe_candidate(self, image):
        path = self.stage / 'candidate.env'
        try:
            with path.open('x', encoding='utf-8') as stream:
                path.chmod(0o600)
                for key, value in self.config['services']['backend']['environment'].items():
                    value = str(value)
                    require('\n' not in value and '\r' not in value, 'multiline_environment_unsupported')
                    stream.write(key + '=' + value + '\n')
            self.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--user', '1000:1000',
                      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '512m',
                      '--pids-limit', '64', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                      '--env-file', str(path), '--entrypoint', 'python', image, '-c',
                      'from app.main import create_app; create_app()'])
        finally:
            path.unlink(missing_ok=True)

    def start(self, images):
        up = self.compose + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate']
        if images['catalog-imports'] != images['backend']:
            self.run(['docker', 'tag', images['catalog-imports'], ALIASES['backend']])
            self.run(up + ['catalog-imports'], timeout=180)
        for name, alias in ALIASES.items():
            self.run(['docker', 'tag', images[name], alias])
        services = list(TARGETS) if images['catalog-imports'] == images['backend'] else ['backend', 'frontend']
        self.run(up + services, timeout=180)

    def backup(self, receipt):
        folder = self.stage / 'backup'
        folder.mkdir(mode=0o700)
        for name, image in receipt['old_images'].items():
            self.run(['docker', 'tag', image, 'lavr-crm-rollback:' + self.stage.name + '-' + name])
        self.run(['docker', 'image', 'save', '-o', str(folder / 'images.tar'), *sorted(set(receipt['old_images'].values()))], timeout=300)
        self.run(['git', 'archive', receipt['old_commit']], output=folder / 'source.tar', timeout=180)
        with tarfile.open(folder / 'configuration.tar', 'w') as archive:
            for name in PROTECTED:
                archive.add(self.root / name, arcname=name, recursive=False)
        private_json(folder / 'caddy-runtime.json', receipt['caddy'])
        self.run(self.compose + ['exec', '-T', 'postgres', 'sh', '-c',
                                'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                 output=folder / 'database.dump', timeout=300)
        with (folder / 'database.dump').open('rb') as stream:
            checked = subprocess.run(self.compose + ['exec', '-T', 'postgres', 'pg_restore', '--list'],
                                     cwd=self.root, stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        require(checked.returncode == 0 and checked.stdout.strip(), 'invalid_database_backup')
        self.run(['docker', 'cp', receipt['baseline']['backend']['id'] + ':/data/media/.', '-'],
                 output=folder / 'media.tar', timeout=300)
        receipt['media_before'] = media_inventory(folder / 'media.tar')
        receipt['backups'] = {}
        for path in folder.iterdir():
            path.chmod(0o600)
            with path.open('rb') as stream:
                receipt['backups'][path.name] = {'bytes': path.stat().st_size,
                                               'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}

    def public_smoke(self):
        url = self.config['services']['https']['environment']['PUBLIC_SITE_URL'].rstrip('/')
        require(url.startswith('https://'), 'invalid_public_url')
        for path in ('/', '/healthz', '/api/catalog'):
            with urllib.request.urlopen(url + path, timeout=30) as response:
                require(response.status == 200, 'public_smoke_failed')

    def save(self, receipt, status):
        receipt['status'] = status
        private_json(self.stage / 'receipt.json', receipt)
        private_json(self.control / 'state.json', {'status': status, 'receipt': str(self.stage / 'receipt.json'),
                                                 'current_commit': receipt['commit'] if status == 'success' else receipt['old_commit'],
                                                 'images': receipt['images'] if status == 'success' else receipt['old_images'],
                                                 'data_verified': status == 'success' or receipt.get('previous_verified', False),
                                                 'protected': receipt['protected']})

    def apply(self, receipt):
        applying, maintenance = False, False
        try:
            self.save(receipt, 'prepared')
            progress('maintenance')
            maintenance = True
            self.set_runtime(maintenance_runtime(receipt['caddy']))
            self.database_guard()
            applying = True
            self.save(receipt, 'applying')
            self.stop()
            self.database_guard()
            self.backup(receipt)
            if receipt['verify_data']:
                receipt['database_before'] = self.database_inventory()
            self.save(receipt, 'applying')
            self.git('checkout', '--detach', receipt['commit'])
            self.start(receipt['images'])
            current = self.verify(receipt['images'], receipt['baseline'], receipt['protected'])
            self.database_guard()
            if receipt['verify_data']:
                receipt['database_after'] = self.database_inventory()
                require(receipt['database_after'] == receipt['database_before'], 'database_content_changed')
                after = self.stage / 'media-after.tar'
                self.run(['docker', 'cp', current['backend']['id'] + ':/data/media/.', '-'], output=after, timeout=300)
                receipt['media_after'] = media_inventory(after)
                require(receipt['media_after'] == receipt['media_before'], 'media_content_changed')
            self.set_runtime(receipt['caddy'])
            maintenance = False
            progress('traffic_restored')
            self.public_smoke()
            self.save(receipt, 'success')
        except BaseException:
            if applying:
                try:
                    progress('rollback')
                    self.set_runtime(maintenance_runtime(receipt['caddy']))
                    self.stop()
                    self.git('checkout', '--detach', receipt['old_commit'])
                    self.start(receipt['old_images'])
                    self.verify(receipt['old_images'], receipt['baseline'], receipt['protected'])
                    self.set_runtime(receipt['caddy'])
                    self.public_smoke()
                    self.save(receipt, 'rolled_back')
                    progress('rolled_back')
                except BaseException:
                    try:
                        self.set_runtime(maintenance_runtime(receipt['caddy']))
                    except BaseException:
                        pass
                    self.save(receipt, 'rollback_failed')
                    raise DeployError('rollback_failed_maintenance_may_remain') from None
            elif maintenance:
                self.set_runtime(receipt['caddy'])
                self.save(receipt, 'rolled_back')
            raise

    def deploy(self, sha):
        old = self.select_commit(sha)
        self.config = json.loads(self.run(self.compose + ['config', '--format', 'json']))
        protected, baseline = self.protected(), self.snapshot()
        require(all(name in baseline and baseline[name]['running'] for name in (*TARGETS, 'postgres', 'https')), 'required_service_not_running')
        require(all(baseline[name]['health'] == 'healthy' for name in ('backend', 'frontend', 'postgres')), 'unhealthy_baseline')
        self.database_guard()
        state_path = self.control / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        if state:
            require(state['status'] in ('success', 'rolled_back'), 'unfinished_deployment_inspect_receipt')
            require(state['current_commit'] == old and state['protected'] == protected, 'deployed_state_drift')
            self.verify(state['images'], baseline, protected)
            if state['status'] == 'success' and state['current_commit'] == sha:
                self.public_smoke()
                return {'status': 'unchanged', 'commit': sha, 'receipt': state['receipt']}
        require(shutil.disk_usage(self.root).free > 5 * 1024**3, 'insufficient_disk')
        self.releases.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dt%H%M%S%fz')
        self.stage = self.releases / ('git-' + stamp + '-' + sha[:12])
        self.stage.mkdir(mode=0o700)
        source = self.stage / 'source'
        previous_umask = os.umask(0o022)
        try:
            self.git('worktree', 'add', '--detach', str(source), sha)
        finally:
            os.umask(previous_umask)
        tag = 'lavr-crm-git:' + self.stage.name
        images = {}
        for name, context in (('backend', source / 'backend'), ('frontend', source)):
            progress('build_' + name)
            args = ['docker', 'build', '-t', tag + '-' + name]
            if name == 'frontend':
                args += ['--build-arg', 'VITE_DATA_MODE=api']
            self.run(args + [str(context)], timeout=2400)
            images[name] = json.loads(self.run(['docker', 'image', 'inspect', tag + '-' + name]))[0]['Id']
            if name == 'backend':
                progress('candidate_import')
                self.probe_candidate(images[name])
        images['catalog-imports'] = images['backend']
        self.verify({name: baseline[name]['image'] for name in TARGETS}, baseline, protected)
        require(self.git('rev-parse', 'HEAD') == old
                and not self.git('status', '--porcelain', '--untracked-files=no'), 'checkout_changed_during_build')
        db = self.database_guard()
        old_images = {name: baseline[name]['image'] for name in TARGETS}
        sizes = json.loads(self.run(['docker', 'image', 'inspect', *sorted(set(old_images.values()))]))
        media_kib = int(self.run(['docker', 'exec', baseline['backend']['id'], 'du', '-sk', '/data/media']).split()[0])
        require(shutil.disk_usage(self.root).free > sum(item['Size'] for item in sizes)
                + db['bytes'] * 2 + media_kib * 2048 + 512 * 1024**2, 'insufficient_backup_disk')
        receipt = {'commit': sha, 'old_commit': old, 'images': images, 'protected': protected,
                   'old_images': old_images, 'previous_verified': bool(state and state.get('data_verified')),
                   'baseline': baseline, 'caddy': self.active_runtime(),
                   'verify_data': not (state and state.get('data_verified'))}
        self.apply(receipt)
        return {'status': 'success', 'commit': sha, 'receipt': str(self.stage / 'receipt.json')}


def main():
    require(len(sys.argv) == 2, 'usage_deploy.py_FULL40SHA')
    operator = Deploy()
    def interrupted(signum, frame):
        raise DeployError('interrupted')
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    with (operator.control / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(operator.deploy(sys.argv[1])))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        print(json.dumps({'status': 'failed', 'reason': str(error) if isinstance(error, DeployError) else type(error).__name__}))
        sys.exit(1)
