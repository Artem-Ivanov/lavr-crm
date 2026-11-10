from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import utcnow
from app.integration import Outbox
from test_accounts import mail_settings, signup


def test_email_worker_sends_while_telegram_disabled_and_cleans_payload(client, db_engine, db, mail_settings, monkeypatch):
    from app.config import settings
    from app.email_worker import process_once
    monkeypatch.setattr(settings, 'telegram_notifications_enabled', False)
    response = signup(client)
    assert response.status_code == 202, response.text
    db.add(Outbox(id='unrelated', event_type='catalog_sync', payload={}, state='queued'))
    db.commit()
    raw = str(db.execute(select(Outbox._payload)).all())
    assert 'new@example.test' not in raw and 'Ваш код' not in raw
    sent = []
    assert process_once(session_factory=lambda: Session(db_engine), send=sent.append)
    db.expire_all()
    email = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert email.state == 'done' and email.payload == {}
    assert db.get(Outbox, 'unrelated').state == 'queued'
    assert len(sent) == 1 and sent[0]['code'] in sent[0]['text']
    assert not process_once(session_factory=lambda: Session(db_engine), send=sent.append)


def test_worker_retries_same_code_after_restart_with_safe_error(client, db_engine, db, mail_settings):
    from app.email_worker import process_once
    signup(client)
    now = utcnow()
    attempts = []
    def fail(payload):
        attempts.append(payload)
        raise OSError('secret SMTP response with email/code')
    assert process_once(session_factory=lambda: Session(db_engine), send=fail, now=now)
    db.expire_all()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert event.last_error_code == 'SMTP_DELIVERY_ERROR'
    assert not process_once(session_factory=lambda: Session(db_engine), send=attempts.append, now=now + timedelta(seconds=4))
    assert process_once(session_factory=lambda: Session(db_engine), send=attempts.append, now=now + timedelta(seconds=6))
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_worker_cancels_expired_generation_and_cleans_expired_pending(client, db_engine, db, mail_settings):
    from app.accounts import UserChallenge
    from app.email_worker import process_once
    response = signup(client)
    flow = db.get(UserChallenge, response.json()['flowId'])
    flow.generation += 1
    db.commit()
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Old generation sent'))
    db.expire_all()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert event.state == 'cancelled' and event.payload == {}
    flow.created_at -= timedelta(days=1)
    db.commit()
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Expired flow sent'))
    db.expire_all()
    assert db.get(UserChallenge, flow.flow_id).encrypted_payload is None
    assert db.get(UserChallenge, flow.flow_id).code_hmac is None


def test_worker_reclaims_lease_and_fences_stale_completion(client, db_engine, db, mail_settings):
    from app.email_worker import process_once
    signup(client)
    now = utcnow()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    event.state, event.attempts, event.lease_until = 'running', 1, now + timedelta(seconds=20)
    db.commit()
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Live lease'), now=now)
    def reclaimed(_):
        with Session(db_engine) as other:
            row = other.get(Outbox, event.id)
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=120)
            other.commit()
    assert process_once(session_factory=lambda: Session(db_engine), send=reclaimed, now=now + timedelta(seconds=21))
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'running'
    assert db.get(Outbox, event.id).attempts == 3


def test_smtp_transport_requires_verified_tls_and_sends_one_message(mail_settings, monkeypatch):
    import app.email_worker as worker
    from app.config import settings
    calls = []
    class SMTP:
        def __init__(self, host, port, **kwargs):
            calls.append(('connect', host, port, kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self):
            calls.append(('ehlo',))
        def starttls(self, **kwargs):
            calls.append(('starttls', kwargs))
        def login(self, user, password):
            calls.append(('login', user, password))
        def send_message(self, message):
            calls.append(('message', message))
    monkeypatch.setattr(worker.smtplib, 'SMTP', SMTP)
    monkeypatch.setattr(worker.smtplib, 'SMTP_SSL', SMTP)
    payload = {'to': 'buyer@example.test', 'subject': 'Code', 'text': 'Code 123456'}
    worker.send_email(payload)
    context = calls[0][3]['context']
    assert context.check_hostname and context.verify_mode == worker.ssl.CERT_REQUIRED
    assert calls[-1][1]['To'] == 'buyer@example.test'
    calls.clear()
    monkeypatch.setattr(settings, 'smtp_tls_mode', 'starttls')
    monkeypatch.setattr(settings, 'smtp_username', 'test-user')
    monkeypatch.setattr(settings, 'smtp_password', 'test-password')
    worker.send_email(payload)
    assert [call[0] for call in calls] == ['connect', 'ehlo', 'starttls', 'ehlo', 'login', 'message']
    monkeypatch.setattr(settings, 'smtp_tls_mode', 'none')
    assert not worker.smtp_configured()
    with pytest.raises(worker.EmailDeliveryError):
        worker.send_email(payload)


def test_unconfigured_worker_cleans_expired_secrets_without_sending(client, db_engine, db, mail_settings, monkeypatch):
    from app.accounts import UserChallenge, ChallengeThrottle
    from app.config import settings
    from app.email_worker import process_once
    response = signup(client)
    now = utcnow() + timedelta(hours=2)
    monkeypatch.setattr(settings, 'smtp_host', '')
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Unconfigured SMTP sent'), now=now)
    db.expire_all()
    assert db.get(UserChallenge, response.json()['flowId']).encrypted_payload is None
    assert db.get(UserChallenge, response.json()['flowId']).code_hmac is None
    assert not db.scalars(select(ChallengeThrottle)).all()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert event.state == 'cancelled' and event.payload == {}


def test_worker_retries_are_bounded_and_terminal_payload_is_removed(client, db_engine, db, mail_settings):
    from app.email_worker import process_once
    signup(client)
    now = utcnow()
    def fail(_):
        raise OSError('sensitive provider detail')
    for delay in (0, 6, 37, 158, 459):
        assert process_once(session_factory=lambda: Session(db_engine), send=fail, now=now + timedelta(seconds=delay))
    db.expire_all()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert event.state == 'rejected' and event.attempts == 5 and event.payload == {}
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Terminal event retried'), now=now + timedelta(seconds=460))


def test_worker_cancels_consumed_flow_before_delivery(client, db_engine, db, mail_settings):
    from app.accounts import UserChallenge
    from app.email_worker import process_once
    response = signup(client)
    flow = db.get(UserChallenge, response.json()['flowId'])
    flow.consumed_at = utcnow()
    db.commit()
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Consumed code sent'))
    db.expire_all()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert event.state == 'cancelled' and event.payload == {}


def test_tls_smtp_provider_error_never_escapes_with_sensitive_response(mail_settings, monkeypatch):
    import app.email_worker as worker
    class SMTP:
        def __init__(self, *args, **kwargs):
            raise worker.smtplib.SMTPAuthenticationError(535, b'user secret@example.test code 123456')
    monkeypatch.setattr(worker.smtplib, 'SMTP_SSL', SMTP)
    with pytest.raises(worker.EmailDeliveryError) as caught:
        worker.send_email({'to': 'buyer@example.test', 'subject': 'Code', 'text': 'Code 123456'})
    assert str(caught.value) == 'SMTP_DELIVERY_ERROR'


@pytest.mark.parametrize('setting,value,error', [('challenge_secret', '', RuntimeError), ('pii_encryption_keys', {}, ValueError)])
def test_worker_fails_fast_before_queue_access_with_missing_secrets(monkeypatch, setting, value, error):
    import app.email_worker as worker
    from app.config import settings
    monkeypatch.setattr(settings, setting, value)
    monkeypatch.setattr(worker, 'process_once', lambda: pytest.fail('Queue accessed before configuration validation'))
    with pytest.raises(error):
        worker.main()


def test_interrupted_fifth_attempt_is_rejected_without_sixth_send(client, db_engine, db, mail_settings):
    from sqlalchemy import update
    from app.email_worker import MAX_ATTEMPTS, process_once
    signup(client)
    now = utcnow()
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    stale_lease = now - timedelta(seconds=1)
    event.state, event.attempts, event.lease_until = 'running', MAX_ATTEMPTS, stale_lease
    db.commit()
    sent = []
    assert process_once(session_factory=lambda: Session(db_engine), send=sent.append, now=now)
    assert sent == []
    db.expire_all()
    event = db.get(Outbox, event.id)
    assert event.state == 'rejected' and event.attempts == MAX_ATTEMPTS and event.payload == {}
    assert event.lease_until is None and event.next_attempt_at is None
    old_completion = db.execute(update(Outbox).where(
        Outbox.id == event.id, Outbox.state == 'running', Outbox.attempts == MAX_ATTEMPTS,
        Outbox.lease_until == stale_lease,
    ).values(state='done'))
    assert old_completion.rowcount == 0
    db.commit()
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'rejected'
    assert not process_once(session_factory=lambda: Session(db_engine), send=sent.append, now=now + timedelta(seconds=1))
    assert sent == []


def test_cleanup_preserves_terminal_ciphertext_and_cancels_expired_pending_only_once(client, db_engine, db, mail_settings):
    from sqlalchemy import literal_column
    from app.accounts import UserChallenge
    from app.email_worker import cleanup
    signup(client)
    now = utcnow()
    pending = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    pending.created_at = now - timedelta(minutes=11)
    flow = db.scalar(select(UserChallenge))
    flow.created_at = now - timedelta(minutes=31)
    for state in ('done', 'cancelled', 'rejected'):
        db.add(Outbox(id='terminal-' + state, event_type='customer_verification_email', state=state,
                      payload={}, created_at=now - timedelta(hours=1)))
    db.commit()
    before = dict(db.execute(select(Outbox.id, Outbox._payload)).all())
    cleanup(db, now)
    db.commit()
    after = dict(db.execute(select(Outbox.id, Outbox._payload)).all())
    for state in ('done', 'cancelled', 'rejected'):
        assert after['terminal-' + state] == before['terminal-' + state]
    assert after[pending.id] != before[pending.id]
    db.expire_all()
    assert db.get(Outbox, pending.id).state == 'cancelled'
    assert db.get(Outbox, pending.id).payload == {}
    assert db.get(UserChallenge, flow.flow_id).encrypted_payload is None
    assert db.get(UserChallenge, flow.flow_id).code_hmac is None
    challenge_version = db.scalar(select(literal_column('xmin::text')).select_from(UserChallenge).where(UserChallenge.flow_id == flow.flow_id))
    cleanup(db, now)
    db.commit()
    assert dict(db.execute(select(Outbox.id, Outbox._payload)).all()) == after
    assert db.scalar(select(literal_column('xmin::text')).select_from(UserChallenge).where(UserChallenge.flow_id == flow.flow_id)) == challenge_version
