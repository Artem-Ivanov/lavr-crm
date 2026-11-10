import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def test_generated_environment_uses_unique_url_safe_secrets():
    from app.init_env import generate_env

    first = dict(line.split('=', 1) for line in generate_env().splitlines() if line and not line.startswith('#'))
    second = dict(line.split('=', 1) for line in generate_env().splitlines() if line and not line.startswith('#'))
    assert first['ADMIN_LOGIN'] == ''
    assert first['APP_MODE'] == 'api'
    assert first['APP_BIND'] == '127.0.0.1'
    assert first['COOKIE_SECURE'] == 'false'
    assert first['SEED_DEMO_CATALOG'] == 'true'
    for key in ['POSTGRES_PASSWORD', 'ADMIN_PASSWORD', 'SESSION_SECRET']:
        assert re.fullmatch(r'[A-Za-z0-9_-]{32,}', first[key])
        assert first[key] != second[key]
    assert len({first[key] for key in ['POSTGRES_PASSWORD', 'ADMIN_PASSWORD', 'SESSION_SECRET']}) == 3


def test_generated_environment_provisions_distinct_pii_keys_with_registration_disabled():
    from app.init_env import generate_env

    def values():
        return dict(line.split('=', 1) for line in generate_env().splitlines() if line and not line.startswith('#'))

    first, second = values(), values()
    assert 'PII_ENCRYPTION_KEYS' in first
    keys = json.loads(first['PII_ENCRYPTION_KEYS'])
    encryption_key = keys[first['PII_ACTIVE_KEY_ID']]
    index_key = first['PII_INDEX_KEY']
    decoded = [base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)) for value in (encryption_key, index_key)]
    assert all(len(value) == 32 for value in decoded)
    assert decoded[0] != decoded[1]
    assert first['CHALLENGE_SECRET'] not in (first['SESSION_SECRET'], encryption_key, index_key)
    for name in ('PII_ENCRYPTION_KEYS', 'PII_INDEX_KEY', 'CHALLENGE_SECRET'):
        assert first[name] != second[name]
    assert first['CUSTOMER_REGISTRATION_ENABLED'] == 'false'
    assert first['SMTP_HOST'] == first['SMTP_PASSWORD'] == ''


def prepare_start_script(tmp_path):
    project = tmp_path / 'project'
    (project / 'scripts').mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / 'scripts/start.sh'
    script = project / 'scripts/start.sh'
    shutil.copyfile(source, script)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    generated = tmp_path / 'generated-env'
    from app.init_env import generate_env
    generated.write_text(generate_env())
    log = tmp_path / 'docker-calls'
    fake_docker = binaries / 'docker'
    fake_docker.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    sys.exit(1 if os.environ.get('FAKE_IMAGE_MISSING') == 'true' else 0)
if args and args[0] == 'run':
    print(pathlib.Path(os.environ['FAKE_ENV_FILE']).read_text(), end='')
    if os.environ.get('FAKE_GENERATOR_FAIL') == 'true':
        sys.exit(23)
''')
    fake_docker.chmod(0o755)
    env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
           'FAKE_ENV_FILE': str(generated), 'FAKE_DOCKER_LOG': str(log)}
    return project, script, generated, log, env


def test_start_stops_after_creating_private_environment_then_preserves_explicit_email_on_restart(tmp_path):
    project, script, generated, log, env = prepare_start_script(tmp_path)
    first = subprocess.run(['sh', str(script)], env={**env, 'FAKE_IMAGE_MISSING': 'true'}, capture_output=True, text=True)
    assert first.returncode != 0
    assert 'ADMIN_LOGIN' in first.stdout
    saved = project / '.env.docker'
    assert saved.read_text() == generated.read_text()
    assert saved.stat().st_mode & 0o777 == 0o600
    for line in generated.read_text().splitlines():
        if line.startswith(('POSTGRES_PASSWORD=', 'ADMIN_PASSWORD=', 'SESSION_SECRET=')):
            assert line.split('=', 1)[1] not in first.stdout + first.stderr
    assert not list(project.glob('.env.docker.tmp.*'))
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(call[0] == 'build' for call in calls)
    assert all(call[0] != 'compose' for call in calls)
    configured = generated.read_text().replace('ADMIN_LOGIN=\n', 'ADMIN_LOGIN=owner@example.test\n')
    saved.write_text(configured)
    log.write_text('')
    second = subprocess.run(['sh', str(script), '--no-build'], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert saved.read_text() == configured
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[0] != 'run' for call in calls)
    assert any('--no-build' in call and '--pull' in call and 'never' in call for call in calls)


def test_preloaded_start_refuses_missing_image_without_creating_secrets(tmp_path):
    project, script, _, log, env = prepare_start_script(tmp_path)
    result = subprocess.run(['sh', str(script), '--no-build'], env={**env, 'FAKE_IMAGE_MISSING': 'true'}, capture_output=True, text=True)
    assert result.returncode != 0
    assert not (project / '.env.docker').exists()
    assert all(json.loads(line)[0] != 'build' for line in log.read_text().splitlines())


def test_fresh_source_start_builds_current_generator_before_creating_environment(tmp_path):
    project, script, _, log, env = prepare_start_script(tmp_path)
    result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    build = next((index for index, call in enumerate(calls) if call[0] == 'build'), None)
    generator = next(index for index, call in enumerate(calls) if call[0] == 'run')
    assert build is not None and build < generator
    assert all(call[0] != 'compose' for call in calls)


def test_generator_failure_removes_temporary_secrets_and_does_not_start_containers(tmp_path):
    project, script, _, log, env = prepare_start_script(tmp_path)
    result = subprocess.run(['sh', str(script), '--no-build'], env={**env, 'FAKE_GENERATOR_FAIL': 'true'}, capture_output=True, text=True)
    assert result.returncode != 0
    assert not (project / '.env.docker').exists()
    assert not list(project.glob('.env.docker.tmp.*'))
    assert all(json.loads(line)[0] != 'compose' for line in log.read_text().splitlines())
