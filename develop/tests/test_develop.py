"""Development-only transport, private setup and isolated Compose checks."""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import runpy
import ssl
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

DEVELOP = Path(__file__).resolve().parents[1]


def load(name):
    path = DEVELOP / f'{name}.py'
    assert path.exists(), f'missing development implementation: {path.name}'
    spec = importlib.util.spec_from_file_location(f'develop_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_preserves_failed_server_exit_status(monkeypatch):
    monkeypatch.setitem(sys.modules, 'internal_notifier', SimpleNamespace(
        server=SimpleNamespace(main=lambda **_kwargs: 1),
        telegram_api=SimpleNamespace(),
    ))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(DEVELOP / 'notifier_mock_launcher.py'), run_name='__main__')
    assert stopped.value.code == 1


@pytest.fixture
def fake():
    module = load('telegram_mock')
    server = module.make_server(('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield module, server.server_address
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(address, method, path, data=None, *, raw=None):
    connection = http.client.HTTPConnection(*address, timeout=3)
    try:
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        connection.request(method, path, body, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.read()
        return response.status, json.loads(result)
    finally:
        connection.close()


def test_success_retry_capture_and_reset(fake):
    module, address = fake
    assert request(address, 'GET', '/healthz') == (200, {'ok': True})
    assert request(address, 'POST', '/control', {'responses': [429, 200]})[0] == 200
    payload = {'chat_id': '-1001234567890', 'text': 'Synthetic test'}
    path = f'/bot{module.FAKE_TOKEN}/sendMessage'
    status, result = request(address, 'POST', path, payload)
    assert status == 429 and result['parameters']['retry_after'] == 1
    status, result = request(address, 'POST', path, payload)
    assert status == 200 and result['ok'] is True
    message_id = result['result']['message_id']
    assert type(message_id) is int and message_id > 0
    attempts = request(address, 'GET', '/messages')[1]['messages']
    assert [attempt['status'] for attempt in attempts] == [429, 200]
    assert attempts[1]['payload'] == payload
    assert attempts[1]['message_id'] == message_id
    assert request(address, 'DELETE', '/messages')[0] == 200
    assert request(address, 'GET', '/messages')[1] == {'messages': []}


def test_rejects_wrong_token_path_and_invalid_input_without_capture(fake):
    module, address = fake
    path = f'/bot{module.FAKE_TOKEN}/sendMessage'
    assert request(address, 'POST', '/botwrong/sendMessage', {})[0] == 404
    assert request(address, 'POST', path + '?unexpected=1', {})[0] == 404
    assert request(address, 'POST', path, raw=b'{')[0] == 400
    assert request(address, 'POST', path, [1, 2])[0] == 400
    assert request(address, 'POST', path, {'chat_id': 'x', 'text': ''})[0] == 400
    assert request(address, 'POST', path, raw=b'x' * 65537)[0] == 413
    assert request(address, 'POST', '/control', {'responses': [True]})[0] == 400
    assert request(address, 'GET', '/messages')[1] == {'messages': []}


def test_scripted_malformed_response_is_actual_invalid_json(fake):
    module, address = fake
    request(address, 'POST', '/control', {'responses': ['malformed']})
    connection = http.client.HTTPConnection(*address, timeout=3)
    connection.request('POST', f'/bot{module.FAKE_TOKEN}/sendMessage', json.dumps({'chat_id': '-1', 'text': 'test'}))
    response = connection.getresponse()
    assert response.status == 200
    with pytest.raises(ValueError):
        json.loads(response.read())
    connection.close()
    assert request(address, 'GET', '/messages')[1]['messages'][0]['message_id'] is None


def test_setup_private_files_valid_tls_and_rerun_preserves_state(tmp_path):
    module = load('setup')
    runtime = tmp_path / '.runtime'
    assert module.generate(runtime) is True
    expected = {'compose.env', 'relay.json', 'tls.crt', 'tls.key', 'ledger'}
    assert set(path.name for path in runtime.iterdir()) == expected
    assert runtime.stat().st_mode & 0o777 == 0o700
    assert (runtime / 'ledger').stat().st_mode & 0o777 == 0o700
    for name in ('compose.env', 'relay.json', 'tls.key'):
        assert (runtime / name).stat().st_mode & 0o777 == 0o600
    values = dict(line.split('=', 1) for line in (runtime / 'compose.env').read_text().splitlines() if line and not line.startswith('#'))
    assert values['POSTGRES_DB'] == 'mirzap67_notifier_test'
    assert len(values['NOTIFICATION_RELAY_TOKEN']) >= 32
    assert len(values['POSTGRES_PASSWORD']) >= 32
    assert values['DEV_TELEGRAM_BOT_TOKEN'] == '123456:development-only-token-000000000'
    assert len(json.loads(values['PII_ENCRYPTION_KEYS'])['v1']) >= 32
    config = json.loads((runtime / 'relay.json').read_text())
    assert config['sources'] == [{'id': 'mirzap67', 'token_sha256': hashlib.sha256(values['NOTIFICATION_RELAY_TOKEN'].encode()).hexdigest(), 'destinations': ['mirzap67.staff'], 'allowed_origins': ['https://crm.test']}]
    assert config['aliases']['mirzap67.staff']['chat_id'] == '-1001234567890'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(runtime / 'tls.crt', runtime / 'tls.key')
    for hostname in ('notifier', 'localhost'):
        result = subprocess.run(['openssl', 'x509', '-in', str(runtime / 'tls.crt'), '-noout', '-checkhost', hostname], capture_output=True, text=True)
        assert result.returncode == 0 and 'does match' in result.stdout
    before = {name: (runtime / name).read_bytes() for name in expected - {'ledger'}}
    (runtime / 'ledger' / 'existing.sqlite').write_text('preserve')
    assert module.generate(runtime) is False
    assert before == {name: (runtime / name).read_bytes() for name in before}
    assert (runtime / 'ledger' / 'existing.sqlite').read_text() == 'preserve'
    second = tmp_path / 'second'
    module.generate(second)
    assert (second / 'compose.env').read_bytes() != before['compose.env']


def test_setup_refuses_partial_runtime(tmp_path):
    module = load('setup')
    runtime = tmp_path / '.runtime'
    runtime.mkdir()
    (runtime / 'compose.env').write_text('keep me')
    with pytest.raises(FileExistsError):
        module.generate(runtime)
    assert (runtime / 'compose.env').read_text() == 'keep me'


def test_compose_has_only_isolated_test_services(tmp_path):
    module = load('setup')
    runtime = tmp_path / '.runtime'
    module.generate(runtime)
    env = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
    result = subprocess.run(['docker', 'compose', '--env-file', str(runtime / 'compose.env'), '-f', str(DEVELOP / 'compose.yaml'), 'config', '--format', 'json'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config['name'] == 'mirzap67-notifier-dev'
    assert set(config['services']) == {'postgres', 'backend', 'notifications', 'notifier', 'telegram-mock'}
    assert config['networks']['default']['internal'] is True
    ports = {'postgres': 55442, 'backend': 8083, 'notifier': 50052, 'telegram-mock': 8093}
    for name, service in config['services'].items():
        assert service.get('restart') == 'no'
        assert service.get('env_file') is None
        if name in ports:
            assert service['ports'][0]['host_ip'] == '127.0.0.1'
            assert int(service['ports'][0]['published']) == ports[name]
    for name in ('backend', 'notifications'):
        service = config['services'][name]
        values = service['environment']
        assert values['TELEGRAM_BOT_TOKEN'] == values['TELEGRAM_CHAT_ID'] == ''
        assert values['NOTIFICATION_TRANSPORT'] == 'relay'
        assert values['NOTIFICATION_RELAY_TARGET'] == 'notifier:50051'
        assert values['PUBLIC_SITE_URL'] == 'https://crm.test'
        assert values['CUSTOMER_REGISTRATION_ENABLED'] == values['SEED_DEMO_CATALOG'] == 'false'
        assert values['SMTP_HOST'] == values['SMTP_PASSWORD'] == ''
        assert values['DATABASE_URL'].endswith('/mirzap67_notifier_test')
        assert any(volume['target'] == '/run/notifier/tls.crt' and volume['read_only'] for volume in service['volumes'])
    assert config['services']['notifier']['command'] == ['python', '/develop/notifier_mock_launcher.py']
    assert config['services']['notifier']['environment']['TELEGRAM_BOT_TOKEN'] == '123456:development-only-token-000000000'
    assert config['services']['backend']['build']['context'] == str(DEVELOP.parent / 'backend')
    assert config['services']['notifier']['build']['context'] == str(DEVELOP.parent.parent / 'internal-notifier')
    assert set(config['services']['notifications']['depends_on']) == {'backend'}
