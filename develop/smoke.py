"""Run inside the development backend: real API/outbox/TLS RPC, fake Telegram only."""
import argparse
import copy
import hashlib
import json
import sys
import time
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


FAKE_TOKEN = '123456:development-only-token-000000000'
FAKE_CHAT = '-1001234567890'
API = 'http://backend:8000'
MOCK = 'http://telegram-mock:8080'
ORIGIN = 'https://crm.test'
HTTP = build_opener(ProxyHandler({}))


def guard(settings, config, fake_token):
    database = urlsplit(settings.database_url)
    assert database.hostname == 'postgres' and database.path == '/mirzap67_notifier_test', 'Dedicated *_test PostgreSQL required'
    assert settings.notification_transport == 'relay' and settings.telegram_notifications_enabled
    assert settings.notification_relay_target == 'notifier:50051', 'Development TLS service required'
    assert settings.notification_relay_ca_file == '/run/notifier/tls.crt'
    assert settings.public_site_url == ORIGIN and settings.allowed_origins == (ORIGIN,)
    assert settings.notification_destination_alias == 'mirzap67.staff'
    assert not settings.telegram_bot_token and not settings.telegram_chat_id, 'Direct Telegram must be unconfigured'
    assert fake_token == FAKE_TOKEN, 'Only the fixed fake bot is permitted'
    expected = {
        'sources': [{'id': 'mirzap67', 'token_sha256': hashlib.sha256(settings.notification_relay_token.encode()).hexdigest(),
                     'destinations': ['mirzap67.staff'], 'allowed_origins': [ORIGIN]}],
        'aliases': {'mirzap67.staff': {'chat_id': FAKE_CHAT, 'revision': 'development-v1'}},
    }
    assert len(settings.notification_relay_token) >= 32 and config == expected, 'Development-only ACL and destination required'


def request(base, path, *, method='GET', body=None, headers=None, status=200):
    data = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data=data, method=method,
                  headers={'Content-Type': 'application/json', **(headers or {})})
    with HTTP.open(req, timeout=5) as response:
        assert response.status == status, f'Unexpected HTTP status on {path}'
        return json.load(response)


def wait_for(check, description, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError('Timed out waiting for ' + description)


def self_test():
    settings = SimpleNamespace(
        database_url='postgresql+psycopg://dev:unused@postgres:5432/mirzap67_notifier_test',
        notification_transport='relay', telegram_notifications_enabled=True,
        notification_relay_target='notifier:50051', notification_relay_ca_file='/run/notifier/tls.crt',
        notification_relay_token='synthetic-source-token-for-guard-self-test',
        public_site_url=ORIGIN, allowed_origins=(ORIGIN,), notification_destination_alias='mirzap67.staff',
        telegram_bot_token='', telegram_chat_id='')
    config = {'sources': [{'id': 'mirzap67', 'token_sha256': hashlib.sha256(settings.notification_relay_token.encode()).hexdigest(),
                          'destinations': ['mirzap67.staff'], 'allowed_origins': [ORIGIN]}],
              'aliases': {'mirzap67.staff': {'chat_id': FAKE_CHAT, 'revision': 'development-v1'}}}
    guard(settings, config, FAKE_TOKEN)
    for field, value in (
        ('database_url', 'postgresql+psycopg://dev:unused@postgres:5432/production'),
        ('database_url', 'postgresql+psycopg://dev:unused@remote:5432/mirzap67_notifier_test'),
        ('notification_transport', 'direct'), ('notification_relay_target', 'real.example:50051'),
        ('public_site_url', 'https://real.example'), ('telegram_bot_token', 'real-token'),
    ):
        changed = copy.copy(settings)
        setattr(changed, field, value)
        try:
            guard(changed, config, FAKE_TOKEN)
        except AssertionError:
            pass
        else:
            raise AssertionError('Guard accepted unsafe ' + field)
    changed = copy.deepcopy(config)
    changed['aliases']['mirzap67.staff']['chat_id'] = '-1009999999999'
    for candidate, token in ((changed, FAKE_TOKEN), (config, 'real-token')):
        try:
            guard(settings, candidate, token)
        except AssertionError:
            pass
        else:
            raise AssertionError('Guard accepted a non-development destination or bot')
    print('Smoke guards: valid development config accepted, 8 unsafe configs rejected; no writes or sends.')


def main():
    import os
    from app.config import settings

    config = json.loads(Path('/run/notifier/relay.json').read_text())
    guard(settings, config, os.environ.get('DEV_TELEGRAM_BOT_TOKEN'))

    from sqlalchemy import func, select, text
    from app.db import SessionLocal
    from app.integration import Outbox
    from app.notification_protocol import notifications_pb2 as pb
    from app.notification_relay_client import RelayClient
    from app.telegram_api import DeliveryError

    with SessionLocal() as db:
        assert db.scalar(text('SELECT current_database()')) == 'mirzap67_notifier_test'
        assert not db.scalar(select(func.count()).select_from(Outbox).where(
            Outbox.event_type == 'feedback_telegram', Outbox.state.not_in(('done', 'rejected', 'cancelled')))), 'Finish existing development backlog before smoke'
    assert request(MOCK, '/healthz')['ok'] is True
    request(API, '/healthz')

    def messages():
        return request(MOCK, '/messages')['messages']

    def for_case(case_id):
        return [item for item in messages() if item['payload'].get('reply_markup', {}).get('inline_keyboard', [[{}]])[0][0].get('url') == f'{ORIGIN}/#crm-feedback?case={case_id}']

    def outbox(case_id):
        with SessionLocal() as db:
            event = db.get(Outbox, case_id)
            assert event is not None and event.event_type == 'feedback_telegram'
            assert event.state not in ('rejected', 'cancelled'), 'Unexpected terminal outbox state'
            return {'id': event.id, 'state': event.state, 'attempts': event.attempts, 'payload': event.payload,
                    'raw_payload': event._payload}

    def done(case_id):
        event = outbox(case_id)
        return event if event['state'] == 'done' else None

    def create(kind):
        key = str(uuid4())
        body = {'type': kind, 'name': 'Synthetic smoke contact', 'email': f'smoke-{key}@example.test',
                'message': 'SYNTHETIC PRIVATE BODY ' + key}
        headers = {'Origin': ORIGIN, 'Idempotency-Key': key}
        receipt = request(API, '/api/feedback', method='POST', body=body, headers=headers, status=202)
        assert request(API, '/api/feedback', method='POST', body=body, headers=headers, status=202) == receipt
        return receipt, body

    def accepted(client, event, body):
        payload = event['payload']
        snapshot = payload['relay_request']
        assert snapshot['request_id'] == event['id']
        assert snapshot['destination_alias'] == 'mirzap67.staff'
        assert snapshot['button']['url'] == f'{ORIGIN}/#crm-feedback?case={event["id"]}'
        assert body['name'] not in snapshot['text'] and body['email'] not in snapshot['text'] and body['message'] not in snapshot['text']
        assert event['raw_payload']['format'] == 1 and 'relay_request' not in event['raw_payload']
        receipt = client.get(payload['relay_notification_id'])
        assert receipt.state == pb.TELEGRAM_ACCEPTED and receipt.telegram_message_id > 0
        assert payload['telegram_message_id'] == receipt.telegram_message_id and not receipt.duplicate_possible
        before = len(messages())
        repeated = client.submit(snapshot)
        assert repeated.notification_id == receipt.notification_id and repeated.telegram_message_id == receipt.telegram_message_id
        time.sleep(2)
        assert len(messages()) == before, 'Idempotent Submit caused another send'
        return receipt

    # Preserve prior mock attempts and the existing ledger: only script the next sends.
    request(MOCK, '/control', method='POST', body={'responses': [200]})
    first, first_body = create('contact')
    client = RelayClient()
    try:
        first_event = wait_for(lambda: done(first['id']), 'first outbox delivery')
        first_receipt = accepted(client, first_event, first_body)
        first_attempts = for_case(first['id'])
        assert len(first_attempts) == 1 and first_attempts[0]['status'] == 200
        assert first_attempts[0]['message_id'] == first_receipt.telegram_message_id
        assert str(first_attempts[0]['payload']['chat_id']) == FAKE_CHAT

        baseline = len(messages())
        snapshot = first_event['payload']['relay_request']
        saved_metadata = client.metadata
        client.metadata = (('authorization', 'Bearer ' + 'wrong-development-source-token-000000000'),)
        try:
            client.submit({**snapshot, 'request_id': str(uuid4())})
        except DeliveryError as error:
            assert error.code == 'RELAY_UNAUTHENTICATED'
        else:
            raise AssertionError('Wrong source credential accepted')
        finally:
            client.metadata = saved_metadata
        try:
            client.submit({**snapshot, 'request_id': str(uuid4()), 'destination_alias': 'forbidden.staff'})
        except DeliveryError as error:
            assert error.code == 'RELAY_PERMISSION_DENIED'
        else:
            raise AssertionError('Destination ACL bypassed')
        assert len(messages()) == baseline

        request(MOCK, '/control', method='POST', body={'responses': [429, 200]})
        second, second_body = create('request')
        wait_for(lambda: for_case(second['id']), 'fake 429 attempt')
        assert for_case(second['id'])[0]['status'] == 429
        retry_snapshot = wait_for(lambda: outbox(second['id'])['payload'].get('relay_request'), 'saved retry snapshot')
        retry_receipt = client.submit(retry_snapshot)
        def retry_wait():
            receipt = client.get(retry_receipt.notification_id)
            return receipt if receipt.state == pb.RETRY_WAIT else None
        paused = wait_for(retry_wait, 'durable 429 retry receipt', timeout=4)
        assert paused.safe_error_code == 'TELEGRAM_RATE_LIMIT' and paused.HasField('next_attempt_at')
        assert 0 < paused.next_attempt_at.ToDatetime(tzinfo=timezone.utc).timestamp() - time.time() <= 30, 'Retry delay must be finite and bounded'
        still_accepted = client.get(first_receipt.notification_id)
        assert still_accepted.state == pb.TELEGRAM_ACCEPTED and still_accepted.telegram_message_id == first_receipt.telegram_message_id
        second_event = wait_for(lambda: done(second['id']), '429 retry and positive outbox receipt')
        assert second_event['payload']['relay_request'] == retry_snapshot
        second_receipt = accepted(client, second_event, second_body)
        assert second_receipt.notification_id == retry_receipt.notification_id
        attempts = for_case(second['id'])
        assert [item['status'] for item in attempts] == [429, 200]
        assert attempts[0]['message_id'] is None and attempts[1]['message_id'] == second_receipt.telegram_message_id
        assert attempts[0]['payload'] == attempts[1]['payload'], 'Retry changed its pinned payload'
        assert len(for_case(first['id'])) == 1, 'Retry resent the earlier accepted event'
        print('PASS: HTTP contact/request + encrypted outbox + external TLS gRPC + fake Bot API; positive receipts, safe CTA, HTTP/RPC idempotency, auth/ACL denial, 429→200 with saved ledger state.')
        print('Synthetic data retained in dedicated *_test DB and development ledger; real Telegram sends: 0.')
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', help='Check development guards without network or writes')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        sys.path.insert(0, '/app')
        main()
