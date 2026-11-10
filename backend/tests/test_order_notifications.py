import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.common import utcnow
from app.config import settings
from app.integration import Outbox
from app.notification_protocol import notifications_pb2 as pb
from app.orders import Order
from app.telegram_api import DeliveryError
from tests.test_notification_relay_client import Relay, relay_settings
from tests.test_notification_worker import notification_settings, queued_feedback


@pytest.fixture
def queued_order(db):
    order = Order(id='заказ /+%42', number='L-20261110-42',
                  created_at=datetime(2026, 11, 10, 10, 45, tzinfo=timezone.utc),
                  customer={'name': 'Частный покупатель', 'phone': '+79991234567', 'email': 'private@example.test'},
                  fulfillment={'method': 'delivery', 'address': 'Секретный адрес'},
                  items=[{'name': 'Скрытый товар', 'quantity': 2}, {'name': 'Другой товар', 'quantity': 1}],
                  total_kopecks=123456, status='new', integration_status='not_connected', version=1)
    event = Outbox(id=str(uuid4()), order_id=order.id, event_type='order_telegram',
                   payload={'orderId': order.id}, state='queued', attempts=0)
    db.add(order)
    db.flush()
    db.add(event)
    db.commit()
    return order, event


def run(db_engine, relay, now):
    from app.notification_worker import process_once

    return process_once(session_factory=lambda: Session(db_engine), now=now,
                        send=lambda _: pytest.fail('Order relay event sent directly'),
                        relay_client_factory=lambda: relay)


def test_order_payload_has_only_summary_and_encoded_crm_link(queued_order, notification_settings):
    from app.notification_worker import notification_payload

    order, _ = queued_order
    payload = notification_payload(order)
    assert payload['text'] == 'Новый заказ №L-20261110-42 от 10-11-2026 13:45 МСК\nСумма: 1234,56 ₽ · Позиций: 2'
    assert payload['reply_markup']['inline_keyboard'][0][0] == {
        'text': 'Открыть заказ', 'url': 'https://shop.example/#crm?order=%D0%B7%D0%B0%D0%BA%D0%B0%D0%B7+%2F%2B%2542',
    }
    assert payload['link_preview_options'] == {'is_disabled': True}
    for private in ('Частный', '+79991234567', 'private@example.test', 'Секретный', 'Скрытый', 'Другой'):
        assert private not in json.dumps(payload, ensure_ascii=False)


def test_order_direct_delivery_is_once_and_does_not_export_to_erp(queued_order, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    order, event = queued_order
    foreign_ids = []
    for kind in ('order_export', 'customer_verification_email', 'catalog_sync'):
        foreign = Outbox(id=str(uuid4()), order_id=order.id if kind == 'order_export' else None,
                         event_type=kind, payload={}, state='queued', attempts=0)
        db.add(foreign)
        foreign_ids.append(foreign.id)
    db.commit()
    sent = []
    factory = lambda: Session(db_engine)
    assert process_once(session_factory=factory, send=lambda payload: (sent.append(payload), 404)[1])
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Duplicate order delivery'))
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'done'
    assert db.get(Outbox, event.id).payload['telegram_message_id'] == 404
    assert all(db.get(Outbox, identifier).state == 'queued' for identifier in foreign_ids)
    assert db.get(Order, order.id).integration_status == 'not_connected'
    assert len(sent) == 1


def test_orders_only_skips_older_feedback_and_email(client, queued_order, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    _, event = queued_order
    feedback_id = queued_feedback(client)
    feedback = db.get(Outbox, feedback_id)
    feedback.created_at = event.created_at - timedelta(seconds=10)
    email = Outbox(id=str(uuid4()), event_type='customer_verification_email', payload={},
                   state='queued', attempts=0, created_at=feedback.created_at)
    db.add(email)
    db.commit()
    factory = lambda: Session(db_engine)
    assert process_once(session_factory=factory, send=lambda _: 405, orders_only=True)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Unrequested feedback sent'), orders_only=True)
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'done'
    assert db.get(Outbox, feedback_id).state == 'queued' and db.get(Outbox, feedback_id).attempts == 0
    assert db.get(Outbox, email.id).state == 'queued' and db.get(Outbox, email.id).attempts == 0


def test_worker_cli_passes_orders_only_flag(monkeypatch, notification_settings):
    import app.notification_worker as worker

    calls = []
    monkeypatch.setattr(worker, 'process_once', lambda **kwargs: calls.append(kwargs))

    def stop(_):
        raise RuntimeError('loop complete')

    monkeypatch.setattr(worker.time, 'sleep', stop)
    with pytest.raises(RuntimeError, match='^loop complete$'):
        worker.main(['--orders-only'])
    assert calls == [{'orders_only': True}]


def test_order_relay_queued_receipt_is_not_delivery_then_polls_message_id(queued_order, db_engine, db, relay_settings):
    order, event = queued_order
    relay, now = Relay(), utcnow()
    assert run(db_engine, relay, now)
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'queued' and 'telegram_message_id' not in saved.payload
    assert saved.payload['relay_notification_id'] == relay.receipt.notification_id
    assert relay.requests[0]['request_id'] == event.id
    assert relay.requests[0]['button']['label'] == 'Открыть заказ'
    assert 'private@example.test' not in json.dumps(relay.requests, ensure_ascii=False)
    relay.receipt.state, relay.receipt.telegram_message_id = pb.TELEGRAM_ACCEPTED, 1407
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'done'
    assert db.get(Outbox, event.id).payload['telegram_message_id'] == 1407
    assert db.get(Order, order.id).integration_status == 'not_connected'
    assert len(relay.requests) == 1 and relay.lookups == [relay.receipt.notification_id]
    assert not run(db_engine, relay, now + timedelta(seconds=20))


def test_order_lost_submit_ack_replays_committed_immutable_snapshot(queued_order, db_engine, db, relay_settings, monkeypatch):
    order, event = queued_order
    relay, now = Relay(), utcnow()
    relay.failure = DeliveryError('RELAY_UNAVAILABLE', uncertain=True)
    submit = relay.submit

    def inspect(snapshot):
        with Session(db_engine) as other:
            assert other.get(Outbox, event.id).payload['relay_request'] == snapshot
        return submit(snapshot)

    relay.submit = inspect
    assert run(db_engine, relay, now)
    db.expire_all()
    assert db.get(Outbox, event.id).state == 'uncertain'
    order.number, order.total_kopecks, order.items = 'CHANGED', 1, []
    db.commit()
    monkeypatch.setattr(settings, 'public_site_url', 'https://changed.example')
    monkeypatch.setattr(settings, 'notification_destination_alias', 'changed.alias')
    relay.failure = None
    assert run(db_engine, relay, now + timedelta(seconds=6))
    assert relay.requests[0] == relay.requests[1]
    assert relay.requests[0]['request_id'] == event.id
    assert relay.requests[0]['request_created_at'] == now.isoformat()
    assert relay.requests[0]['destination_alias'] == 'mirzap67.staff'
    assert 'L-20261110-42' in relay.requests[1]['text'] and '1234,56' in relay.requests[1]['text']
    assert relay.requests[1]['button']['url'].startswith('https://shop.example/#crm?order=')


def test_order_relay_retry_wait_and_uncertainty_poll_without_resubmit(queued_order, db_engine, db, relay_settings):
    _, event = queued_order
    relay, now = Relay(), utcnow()
    relay.receipt.state = pb.RETRY_WAIT
    relay.receipt.next_attempt_at.FromDatetime(now + timedelta(seconds=60))
    assert run(db_engine, relay, now)
    assert not run(db_engine, relay, now + timedelta(seconds=30))
    relay.receipt.state, relay.receipt.duplicate_possible = pb.UNCERTAIN, True
    relay.receipt.ClearField('next_attempt_at')
    assert run(db_engine, relay, now + timedelta(seconds=61))
    relay.failure = DeliveryError('RELAY_RESOURCE_EXHAUSTED')
    assert run(db_engine, relay, now + timedelta(seconds=67))
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'uncertain' and saved.last_error_code == 'RELAY_RESOURCE_EXHAUSTED'
    assert saved.payload['relay_duplicate_possible'] is True
    assert len(relay.requests) == 1 and relay.lookups == [relay.receipt.notification_id] * 2


@pytest.mark.parametrize('change', ['mode', 'target', 'credential', 'missing'])
def test_order_relay_never_falls_back_to_direct(queued_order, db_engine, db, relay_settings, monkeypatch, change):
    _, event = queued_order
    relay, now = Relay(), utcnow()
    assert run(db_engine, relay, now)
    monkeypatch.setattr(settings, 'telegram_bot_token', 'direct-token')
    monkeypatch.setattr(settings, 'telegram_chat_id', '-100000')
    changes = {'mode': ('notification_transport', 'direct'),
               'target': ('notification_relay_target', 'changed.example:50051'),
               'credential': ('notification_relay_token', 'changed-token'),
               'missing': ('notification_relay_token', '')}
    monkeypatch.setattr(settings, *changes[change])
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state != 'done' and len(relay.requests) == 1
    if change == 'mode':
        assert relay.lookups == [relay.receipt.notification_id]
    else:
        assert saved.last_error_code == 'RELAY_CONFIGURATION_CHANGED' and not relay.lookups


@pytest.mark.parametrize('message_id', [None, 0, -1, True])
def test_order_direct_requires_positive_integer_message_id(queued_order, db_engine, db, notification_settings, message_id):
    from app.notification_worker import process_once

    _, event = queued_order
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: message_id)
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'uncertain' and saved.last_error_code == 'TELEGRAM_INVALID_RESPONSE'
    assert 'telegram_message_id' not in saved.payload


@pytest.mark.parametrize('transport', ['direct', 'relay'])
def test_missing_order_is_terminal_with_safe_error_code(db_engine, db, relay_settings, notification_settings, monkeypatch, transport):
    from app.notification_worker import process_once

    monkeypatch.setattr(settings, 'notification_transport', transport)
    event = Outbox(id=str(uuid4()), event_type='order_telegram', payload={'orderId': 'missing'}, state='queued', attempts=0)
    db.add(event)
    db.commit()
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Missing order sent'),
                        relay_client_factory=lambda: pytest.fail('Missing order submitted'))
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'rejected' and saved.last_error_code == 'ORDER_NOT_FOUND'
    assert saved.next_attempt_at is None


def test_order_worker_respects_live_lease_then_recovers_expired_lease(queued_order, db_engine, db, notification_settings):
    from app.notification_worker import process_once

    _, event = queued_order
    now = utcnow()
    event.state, event.attempts, event.lease_until = 'running', 1, now + timedelta(seconds=20)
    db.commit()
    factory = lambda: Session(db_engine)
    assert not process_once(session_factory=factory, send=lambda _: pytest.fail('Live lease sent'), now=now)
    assert process_once(session_factory=factory, send=lambda _: 1408, now=now + timedelta(seconds=21))
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'done' and saved.attempts == 2


def test_order_stale_worker_cannot_record_receipt(queued_order, db_engine, db, relay_settings):
    _, event = queued_order
    relay, now = Relay(), utcnow()
    submit = relay.submit

    def reclaimed(snapshot):
        with Session(db_engine) as other:
            saved = other.get(Outbox, event.id)
            saved.attempts += 1
            saved.lease_until = now + timedelta(seconds=120)
            other.commit()
        return submit(snapshot)

    relay.submit = reclaimed
    assert run(db_engine, relay, now)
    db.expire_all()
    saved = db.get(Outbox, event.id)
    assert saved.state == 'running' and saved.attempts == 2
    assert 'relay_notification_id' not in saved.payload
