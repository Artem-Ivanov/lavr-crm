import http.client
import json

import pytest


@pytest.fixture
def telegram_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'telegram_bot_token', '123456:secret-token')
    monkeypatch.setattr(settings, 'telegram_chat_id', '-1001234567890')
    monkeypatch.setattr(settings, 'public_site_url', 'https://crm.example')


@pytest.fixture
def telegram_http(monkeypatch, telegram_settings):
    import app.telegram_api as api

    replies, requests, connections = [], [], []

    class Reply:
        def __init__(self, status, data):
            self.status, self.raw = status, data if isinstance(data, bytes) else json.dumps(data).encode()

        def read(self, size):
            assert size == 65537
            return self.raw[:size]

    class Connection:
        def __init__(self, host, *, timeout):
            assert host == 'api.telegram.org' and timeout == 10
            self.closed = False
            connections.append(self)

        def request(self, verb, path, body, headers):
            requests.append((verb, path.rsplit('/', 1)[-1], json.loads(body), headers))

        def getresponse(self):
            response = replies.pop(0)
            if isinstance(response, Exception):
                raise response
            return Reply(*response)

        def close(self):
            self.closed = True

    monkeypatch.setattr(api.http.client, 'HTTPSConnection', Connection)
    return replies, requests, connections


def valid_preflight(replies):
    replies.extend([
        (200, {'ok': True, 'result': {'id': 123456, 'is_bot': True, 'first_name': 'secret title'}}),
        (200, {'ok': True, 'result': {'id': -1001234567890, 'type': 'channel', 'title': 'secret channel'}}),
        (200, {'ok': True, 'result': {
            'status': 'administrator', 'user': {'id': 123456, 'is_bot': True, 'first_name': 'secret title'},
            'can_post_messages': True,
        }}),
    ])


def test_check_validates_identity_channel_and_posting_without_sending(telegram_http):
    from app.telegram_api import check_connection

    replies, requests, connections = telegram_http
    valid_preflight(replies)
    assert check_connection() == {'bot_id': 123456, 'chat_id': -1001234567890}
    assert [(method, payload) for _, method, payload, _ in requests] == [
        ('getMe', {}), ('getChat', {'chat_id': '-1001234567890'}),
        ('getChatMember', {'chat_id': -1001234567890, 'user_id': 123456}),
    ]
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(('position', 'replacement', 'code'), [
    (0, {'id': 123456, 'is_bot': False}, 'TELEGRAM_INVALID_BOT'),
    (0, {'id': True, 'is_bot': True}, 'TELEGRAM_INVALID_BOT'),
    (1, {'id': -1001234567890, 'type': 'supergroup'}, 'TELEGRAM_NOT_CHANNEL'),
    (1, {'id': 'not-an-id', 'type': 'channel'}, 'TELEGRAM_INVALID_RESPONSE'),
    (2, {'status': 'member', 'user': {'id': 123456, 'is_bot': True}}, 'TELEGRAM_NO_POST_PERMISSION'),
    (2, {'status': 'administrator', 'user': {'id': 123456, 'is_bot': True}, 'can_post_messages': False}, 'TELEGRAM_NO_POST_PERMISSION'),
    (2, {'status': 'administrator', 'user': {'id': 999999, 'is_bot': True}, 'can_post_messages': True}, 'TELEGRAM_INVALID_BOT'),
])
def test_preflight_rejects_wrong_bot_or_destination(telegram_http, position, replacement, code):
    from app.telegram_api import DeliveryError, check_connection

    replies, requests, _ = telegram_http
    valid_preflight(replies)
    replies[position] = (200, {'ok': True, 'result': replacement})
    with pytest.raises(DeliveryError) as caught:
        check_connection()
    assert caught.value.code == code
    assert not caught.value.uncertain
    assert len(requests) == position + 1


@pytest.mark.parametrize(('status', 'data', 'code', 'retry'), [
    (429, {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 93}}, 'TELEGRAM_RATE_LIMIT', 93),
    (200, {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 75}}, 'TELEGRAM_RATE_LIMIT', 75),
    (429, {'ok': False, 'parameters': {'retry_after': True}}, 'TELEGRAM_RATE_LIMIT', 60),
    (429, {'ok': False, 'parameters': {'retry_after': -5}}, 'TELEGRAM_RATE_LIMIT', 60),
    (429, {'ok': False, 'parameters': {'retry_after': 10 ** 30}}, 'TELEGRAM_RATE_LIMIT', 604800),
    (401, {'ok': False, 'description': '123456:secret-token'}, 'TELEGRAM_AUTH_ERROR', 900),
    (200, {'ok': False, 'error_code': 403}, 'TELEGRAM_FORBIDDEN', 900),
    (400, {'ok': False, 'description': '123456:secret-token'}, 'TELEGRAM_REJECTED', 900),
    (503, b'<server unavailable>', 'TELEGRAM_UNAVAILABLE', 0),
])
def test_send_parses_safe_api_errors_without_retry(telegram_http, status, data, code, retry):
    from app.telegram_api import DeliveryError, send_telegram

    replies, requests, connections = telegram_http
    replies.append((status, data))
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'chat_id': '-1001234567890', 'text': 'technical test'})
    assert (caught.value.code, caught.value.retry_after) == (code, retry)
    assert 'secret-token' not in str(caught.value)
    assert len(requests) == 1 and connections[0].closed


@pytest.mark.parametrize('raw', [b'not json', b'{}', b'[' * 20000 + b']' * 20000, b'x' * 65537, b'{"ok":true,"result":{"message_id":true}}'], ids=['invalid-json', 'missing-result', 'too-deep', 'oversized', 'bool-id'])
def test_send_marks_malformed_or_oversized_reply_uncertain(telegram_http, raw):
    from app.telegram_api import DeliveryError, send_telegram

    replies, _, _ = telegram_http
    replies.append((200, raw))
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'text': 'technical test'})
    assert caught.value.code == 'TELEGRAM_INVALID_RESPONSE'
    assert caught.value.uncertain


@pytest.mark.parametrize('operation', ['send', 'check'])
@pytest.mark.parametrize('status', [502, 503])
def test_server_failure_leaves_send_outcome_uncertain(telegram_http, operation, status):
    from app.telegram_api import DeliveryError, check_connection, send_telegram

    replies, _, _ = telegram_http
    replies.append((status, b'upstream unavailable'))
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'text': 'technical test'}) if operation == 'send' else check_connection()
    assert caught.value.code == 'TELEGRAM_UNAVAILABLE'
    assert caught.value.uncertain is (operation == 'send')


@pytest.mark.parametrize('operation', ['send', 'check'])
def test_network_failure_is_safe_and_only_send_has_uncertain_delivery(telegram_http, operation):
    from app.telegram_api import DeliveryError, check_connection, send_telegram

    replies, requests, connections = telegram_http
    replies.append(http.client.RemoteDisconnected('https://api.telegram.org/bot123456:secret-token/sendMessage'))
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'text': 'technical test'}) if operation == 'send' else check_connection()
    assert caught.value.code == 'TELEGRAM_NETWORK_ERROR'
    assert caught.value.uncertain is (operation == 'send')
    assert 'secret-token' not in str(caught.value)
    assert len(requests) == 1 and connections[0].closed


def test_send_returns_confirmed_message_id(telegram_http):
    from app.telegram_api import send_telegram

    replies, _, _ = telegram_http
    replies.append((200, {'ok': True, 'result': {'message_id': 42}}))
    assert send_telegram({'text': 'technical test'}) == 42


@pytest.mark.parametrize('origin', ['javascript:alert(1)', 'https://user:pass@crm.example', 'https://:@crm.example', 'https://@crm.example', 'https://crm.example/private', 'https://crm.example?token=secret', 'https://crm.example/#crm', 'https://crm.example:wrong', 'https://crm.example\n'])
def test_invalid_site_is_rejected_before_network(telegram_http, monkeypatch, origin):
    from app.config import settings
    from app.telegram_api import DeliveryError, check_connection

    monkeypatch.setattr(settings, 'public_site_url', origin)
    with pytest.raises(DeliveryError) as caught:
        check_connection()
    assert caught.value.code == 'TELEGRAM_INVALID_SITE_URL'
    assert telegram_http[1] == []


def test_cli_checks_channel_before_crm_has_a_public_address(telegram_http, monkeypatch, capsys):
    from app.config import settings
    from app.telegram_cli import main

    monkeypatch.setattr(settings, 'public_site_url', '')
    valid_preflight(telegram_http[0])
    assert main(['check']) == 0
    output = capsys.readouterr()
    assert 'PUBLIC_SITE_URL' in output.out


def test_missing_settings_are_rejected_before_network(telegram_http, monkeypatch):
    from app.config import settings
    from app.telegram_api import DeliveryError, check_connection

    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    with pytest.raises(DeliveryError) as caught:
        check_connection()
    assert caught.value.code == 'TELEGRAM_NOT_CONFIGURED'
    assert telegram_http[1] == []


def test_cli_check_never_sends_and_prints_only_safe_ids(telegram_http, capsys):
    from app.telegram_cli import main

    replies, requests, _ = telegram_http
    valid_preflight(replies)
    assert main(['check']) == 0
    output = capsys.readouterr()
    assert '123456' in output.out and '-1001234567890' in output.out
    assert 'secret' not in output.out + output.err
    assert [method for _, method, _, _ in requests] == ['getMe', 'getChat', 'getChatMember']


def test_cli_test_checks_connection_then_calls_shared_sender_once(telegram_http, capsys, monkeypatch):
    from app.telegram_cli import main
    from app import notification_worker

    replies, requests, _ = telegram_http
    valid_preflight(replies)
    sends = []

    def send_test():
        assert [method for _, method, _, _ in requests] == ['getMe', 'getChat', 'getChatMember']
        sends.append(True)
        return 42

    monkeypatch.setattr(notification_worker, 'send_test_notification', send_test, raising=False)
    assert main(['test']) == 0
    assert len(sends) == 1
    output = capsys.readouterr()
    assert '42' in output.out and 'secret' not in output.out + output.err


def test_cli_unknown_outcome_explains_duplicate_risk_and_does_not_retry(telegram_http, capsys, monkeypatch):
    from app.telegram_api import DeliveryError
    from app.telegram_cli import main
    from app import notification_worker

    replies, _, _ = telegram_http
    valid_preflight(replies)
    sends = []

    def send_test():
        sends.append(True)
        raise DeliveryError('TELEGRAM_NETWORK_ERROR', uncertain=True)

    monkeypatch.setattr(notification_worker, 'send_test_notification', send_test, raising=False)
    assert main(['test']) == 1
    output = capsys.readouterr()
    assert 'TELEGRAM_NETWORK_ERROR' in output.err
    assert 'повтор' in output.err.lower() and 'дубл' in output.err.lower()
    assert 'secret' not in output.out + output.err
    assert len(sends) == 1


def test_cli_unexpected_sender_failure_is_safe(telegram_http, capsys, monkeypatch):
    from app.telegram_cli import main
    from app import notification_worker

    valid_preflight(telegram_http[0])

    def broken_sender():
        raise RuntimeError('postgres://private credentials 123456:secret-token')

    monkeypatch.setattr(notification_worker, 'send_test_notification', broken_sender, raising=False)
    assert main(['test']) == 1
    output = capsys.readouterr()
    assert 'TELEGRAM_CHECK_ERROR' in output.err
    assert 'secret' not in output.out + output.err
    assert 'postgres' not in output.out + output.err


def test_cli_rejects_arguments_without_echoing_them(telegram_http, capsys):
    from app.telegram_cli import main

    assert main(['check', '123456:secret-token']) == 2
    output = capsys.readouterr()
    assert 'secret' not in output.out + output.err
    assert telegram_http[1] == []
