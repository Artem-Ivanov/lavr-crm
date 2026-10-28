from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.common import utcnow
from app.integration import Outbox


@pytest.fixture
def notification_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'telegram_bot_token', 'test-bot-token', raising=False)
    monkeypatch.setattr(settings, 'telegram_chat_id', '-1000000000000', raising=False)
    monkeypatch.setattr(settings, 'public_site_url', 'https://shop.example', raising=False)
    monkeypatch.setattr(settings, 'telegram_notifications_enabled', True, raising=False)


def queued_feedback(client):
    response = client.post('/api/feedback', json={
        'type': 'request', 'name': 'Тест', 'phone': '', 'email': 'qa@example.test',
        'messenger': {'service': '', 'address': ''}, 'message': 'Текст только для карточки',
    }, headers={'Origin': 'http://testserver', 'Idempotency-Key': str(uuid4())})
    assert response.status_code == 202
    return response.json()['id']


def test_worker_delivers_private_case_link_once_and_ignores_erp(client, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    feedback_id = queued_feedback(client)
    unrelated_id = str(uuid4())
    db.add(Outbox(id=unrelated_id, event_type='catalog_sync', payload={}, state='queued', attempts=0))
    db.commit()
    sent = []
    factory = lambda: Session(db_engine)
    assert process_once(session_factory=factory, send=sent.append)
    assert not process_once(session_factory=factory, send=sent.append)
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'done'
    assert db.get(Outbox, unrelated_id).state == 'queued'
    assert len(sent) == 1
    assert sent[0]['reply_markup']['inline_keyboard'][0][0] == {
        'text': 'Открыть обращение', 'url': f'https://shop.example/#crm-feedback?case={feedback_id}',
    }
    assert 'qa@example.test' not in sent[0]['text']
    assert 'Текст только для карточки' not in sent[0]['text']


def test_worker_leaves_unconfigured_notifications_pending(client, db_engine, db, monkeypatch, notification_settings):
    from app.config import settings
    from app.notification_worker import process_once

    feedback_id = queued_feedback(client)
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Unexpected send'))
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'queued'
    assert db.get(Outbox, feedback_id).attempts == 0


def test_worker_keeps_queue_paused_until_explicitly_enabled(client, db_engine, db, monkeypatch, notification_settings):
    from app.config import settings
    from app.notification_worker import process_once

    feedback_id = queued_feedback(client)
    monkeypatch.setattr(settings, 'telegram_notifications_enabled', False)
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Paused queue sent a message'))
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'queued'
    assert db.get(Outbox, feedback_id).attempts == 0


def test_rate_limit_pauses_other_notifications_across_worker_sessions(client, db_engine, db, notification_settings):
    from app.notification_worker import DeliveryError, process_once

    first_id = queued_feedback(client)
    second_id = queued_feedback(client)
    factory = lambda: Session(db_engine)
    now = utcnow()

    def throttled(_):
        raise DeliveryError('TELEGRAM_RATE_LIMIT', retry_after=70)

    assert process_once(session_factory=factory, send=throttled, now=now)
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Another event bypassed retry_after'), now=now + timedelta(seconds=20))
    db.expire_all()
    assert db.get(Outbox, first_id).state == 'error'
    assert db.get(Outbox, second_id).state == 'queued'
    assert db.get(Outbox, second_id).attempts == 0
    assert process_once(session_factory=factory, send=lambda _: None, now=now + timedelta(seconds=71))


def test_explicit_test_sends_one_technical_message_without_a_case(db_engine, db, monkeypatch, notification_settings):
    from app.config import settings
    from app.notification_worker import send_test_notification

    monkeypatch.setattr(settings, 'telegram_notifications_enabled', False)
    sent = []

    def deliver(payload):
        sent.append(payload)
        return 321

    assert send_test_notification(session_factory=lambda: Session(db_engine), send=deliver) == 321
    assert len(sent) == 1
    assert sent[0]['reply_markup']['inline_keyboard'][0][0]['url'] == 'https://shop.example/#crm-feedback'
    assert 'Тест' in sent[0]['text']
    assert db.query(Outbox).count() == 0


def test_explicit_test_obeys_and_persists_channel_pause(db_engine, notification_settings):
    from app.notification_worker import DeliveryError, send_test_notification

    factory = lambda: Session(db_engine)
    now = utcnow()
    attempts = []

    def throttled(payload):
        attempts.append(payload)
        raise DeliveryError('TELEGRAM_RATE_LIMIT', retry_after=90)

    with pytest.raises(DeliveryError):
        send_test_notification(session_factory=factory, send=throttled, now=now)
    with pytest.raises(DeliveryError) as caught:
        send_test_notification(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Test bypassed channel pause'), now=now + timedelta(seconds=20))
    assert caught.value.retry_after >= 70
    assert len(attempts) == 1
    assert send_test_notification(session_factory=factory, send=lambda _: 322, now=now + timedelta(seconds=91)) == 322


def test_explicit_test_can_check_delivery_without_a_public_crm_url(db_engine, monkeypatch, notification_settings):
    from app.config import settings
    from app.notification_worker import send_test_notification

    monkeypatch.setattr(settings, 'public_site_url', '')
    sent = []

    def deliver(payload):
        sent.append(payload)
        return 323

    assert send_test_notification(session_factory=lambda: Session(db_engine), send=deliver) == 323
    assert len(sent) == 1 and 'reply_markup' not in sent[0]


def test_worker_persists_retry_after_and_does_not_retry_early(client, db_engine, db, notification_settings):
    from app.notification_worker import DeliveryError, process_once

    feedback_id = queued_feedback(client)
    now = utcnow()

    def throttled(_):
        raise DeliveryError('TELEGRAM_RATE_LIMIT', retry_after=70)

    factory = lambda: Session(db_engine)
    assert process_once(session_factory=factory, send=throttled, now=now)
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'error' and event.last_error_code == 'TELEGRAM_RATE_LIMIT'
    assert event.next_attempt_at >= now + timedelta(seconds=70)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Early retry'), now=now + timedelta(seconds=20))
    assert process_once(session_factory=factory, send=lambda _: None, now=now + timedelta(seconds=71))


def test_worker_recovers_expired_lease_but_not_live_lease(client, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    feedback_id = queued_feedback(client)
    now = utcnow()
    event = db.get(Outbox, feedback_id)
    event.state, event.attempts, event.lease_until = 'running', 1, now + timedelta(seconds=20)
    db.commit()
    factory = lambda: Session(db_engine)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Live lease'), now=now)
    assert process_once(session_factory=factory, send=lambda _: None, now=now + timedelta(seconds=21))
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'done'
    assert db.get(Outbox, feedback_id).attempts == 2


def test_old_worker_cannot_finish_a_reclaimed_attempt(client, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    feedback_id = queued_feedback(client)
    now = utcnow()

    def reclaimed(_):
        with Session(db_engine) as other:
            event = other.get(Outbox, feedback_id)
            event.attempts += 1
            event.lease_until = now + timedelta(seconds=120)
            other.commit()

    assert process_once(session_factory=lambda: Session(db_engine), send=reclaimed, now=now)
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'running' and event.attempts == 2


def test_transport_rejects_invalid_telegram_reply_and_obeys_429(monkeypatch, notification_settings):
    from app.notification_worker import DeliveryError, send_telegram
    import app.telegram_api as telegram_api

    class Reply:
        status = 429

        def read(self, _):
            return b'{"ok":false,"parameters":{"retry_after":93}}'

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(telegram_api.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'text': 'Тест'})
    assert caught.value.code == 'TELEGRAM_RATE_LIMIT'
    assert caught.value.retry_after == 93
    Reply.status = 200
    with pytest.raises(DeliveryError) as caught:
        send_telegram({'text': 'Тест'})
    assert caught.value.code == 'TELEGRAM_INVALID_RESPONSE'
