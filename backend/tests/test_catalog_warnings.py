from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import utcnow
from app.config import settings
from app.integration import IntegrationState, Outbox
from app.notification_protocol import notifications_pb2 as pb
from app.telegram_api import DeliveryError
from tests.test_notification_relay_client import Relay, relay_settings
from tests.test_notification_worker import notification_settings
from test_orders import order_product


def stale_catalog(db):
    db.get(IntegrationState, 1).catalog_updated_at = utcnow() - timedelta(days=2)
    db.commit()


@pytest.mark.parametrize('orders_only', [False, True])
def test_stale_warning_uses_configured_channel_once_per_observation(client, order_product, db, db_engine, notification_settings, orders_only):
    from app.notification_worker import process_once

    stale_catalog(db)
    sent = []
    factory = lambda: Session(db_engine)
    assert process_once(session_factory=factory, send=lambda payload: (sent.append(payload), 406)[1], orders_only=orders_only)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Duplicate warning'), orders_only=orders_only)
    db.expire_all()
    events = db.scalars(select(Outbox)).all()
    assert len(events) == 1
    assert events[0].event_type == 'catalog_stale_telegram' and events[0].state == 'done'
    assert events[0].payload['telegram_message_id'] == 406
    assert sent[0]['chat_id'] == '-1000000000000'
    assert 'Каталог требует обновления' in sent[0]['text']
    assert 'Приём заказов продолжается' in sent[0]['text']
    assert sent[0]['reply_markup']['inline_keyboard'][0][0]['url'] == 'https://shop.example/#crm-catalog'

    db.get(IntegrationState, 1).catalog_updated_at -= timedelta(days=1)
    db.commit()
    assert process_once(session_factory=factory, send=lambda payload: (sent.append(payload), 407)[1], orders_only=orders_only)
    assert len(sent) == 2


def test_fresh_catalog_and_disabled_notifications_do_not_alert(client, order_product, db, db_engine, notification_settings, monkeypatch):
    from app.notification_worker import process_once

    factory = lambda: Session(db_engine)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Fresh warning'))
    stale_catalog(db)
    monkeypatch.setattr(settings, 'telegram_notifications_enabled', False)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Disabled warning'))
    assert db.scalars(select(Outbox)).all() == []


@pytest.mark.parametrize('initial_state', ['unconfigured', 'failed'])
@pytest.mark.parametrize('new_observation_age', [0, 3])
def test_pending_warning_is_cancelled_after_observation_changes(client, order_product, db, db_engine, notification_settings, monkeypatch, initial_state, new_observation_age):
    from app.notification_worker import process_once

    stale_catalog(db)
    factory, now = lambda: Session(db_engine), utcnow()
    if initial_state == 'unconfigured':
        monkeypatch.setattr(settings, 'telegram_bot_token', '')
        assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Unconfigured send'), now=now)
        monkeypatch.setattr(settings, 'telegram_bot_token', 'test-bot-token')
    else:
        def fail(_):
            raise DeliveryError('TELEGRAM_NETWORK_ERROR')
        assert process_once(session_factory=factory, send=fail, now=now)
    db.expire_all()
    warning = db.scalar(select(Outbox))
    db.get(IntegrationState, 1).catalog_updated_at = now - timedelta(days=new_observation_age)
    db.commit()
    assert process_once(session_factory=factory, send=lambda _: pytest.fail('Obsolete warning sent'), now=now + timedelta(seconds=6))
    db.refresh(warning)
    assert warning.state == 'cancelled' and warning.lease_until is None and warning.next_attempt_at is None


def test_stale_warning_relay_retains_snapshot_and_polls_receipt(client, order_product, db, db_engine, relay_settings, monkeypatch):
    from app.notification_worker import process_once

    stale_catalog(db)
    now, relay = utcnow(), Relay()
    factory = lambda: Session(db_engine)
    def run(at):
        return process_once(session_factory=factory, now=at, orders_only=True,
                            send=lambda _: pytest.fail('Relay warning sent directly'), relay_client_factory=lambda: relay)

    assert run(now)
    db.expire_all()
    event = db.scalar(select(Outbox))
    assert event.state == 'queued'
    request = event.payload['relay_request']
    assert request['destination_alias'] == 'mirzap67.staff'
    assert request['button']['url'] == 'https://shop.example/#crm-catalog'
    db.get(IntegrationState, 1).catalog_updated_at = now
    db.commit()
    monkeypatch.setattr(settings, 'public_site_url', 'https://changed.example')
    relay.receipt.state, relay.receipt.telegram_message_id = pb.TELEGRAM_ACCEPTED, 408
    assert run(now + timedelta(seconds=6))
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'done'
    assert db.get(Outbox, event.id).payload['relay_request'] == request
    assert db.get(Outbox, event.id).payload['telegram_message_id'] == 408
    assert len(relay.requests) == 1
    assert not run(now + timedelta(seconds=12))
