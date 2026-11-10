from datetime import timedelta
from uuid import uuid4

import grpc
import pytest
from sqlalchemy.orm import Session

from app.common import utcnow
from app.config import settings
from app.integration import Outbox
from app.notification_protocol import notifications_pb2 as pb
from tests.test_notification_worker import queued_feedback


@pytest.fixture
def relay_settings(monkeypatch):
    for name, value in {'notification_transport': 'relay', 'telegram_notifications_enabled': True,
                        'notification_relay_target': 'relay.example:50051',
                        'notification_relay_token': 'source-credential-with-at-least-32-characters',
                        'notification_relay_ca_file': '', 'notification_destination_alias': 'mirzap67.staff',
                        'public_site_url': 'https://shop.example', 'telegram_bot_token': '', 'telegram_chat_id': ''}.items():
        monkeypatch.setattr(settings, name, value, raising=False)


class Relay:
    def __init__(self):
        self.requests, self.lookups = [], []
        self.receipt = pb.NotificationReceipt(notification_id=str(uuid4()), state=pb.QUEUED)
        self.failure = None

    def close(self):
        pass

    def submit(self, snapshot):
        self.requests.append(snapshot)
        if self.failure: raise self.failure
        return self.receipt

    def get(self, notification_id):
        self.lookups.append(notification_id)
        if self.failure: raise self.failure
        return self.receipt


def run(db_engine, relay, now):
    from app.notification_worker import process_once
    return process_once(session_factory=lambda: Session(db_engine), now=now,
                        send=lambda _: pytest.fail('Relay event sent directly'), relay_client_factory=lambda: relay)


def test_source_submit_acceptance_is_not_sent_then_polls_actual_message_id(client, db_engine, db, relay_settings):
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    assert run(db_engine, relay, now)
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'queued' and event.payload['relay_notification_id'] == relay.receipt.notification_id
    assert len(relay.requests) == 1 and not relay.lookups
    relay.receipt.state = pb.TELEGRAM_ACCEPTED
    relay.receipt.telegram_message_id = 1307
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'done' and event.payload['telegram_message_id'] == 1307
    assert len(relay.requests) == 1 and relay.lookups == [relay.receipt.notification_id]
    assert not run(db_engine, relay, now + timedelta(seconds=20))


def test_lost_submit_ack_replays_saved_immutable_snapshot_and_expiry_never_renews_key(client, db_engine, db, relay_settings, monkeypatch):
    from app.telegram_api import DeliveryError
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    relay.failure = DeliveryError('RELAY_UNAVAILABLE', uncertain=True)
    assert run(db_engine, relay, now)
    saved = relay.requests[0]
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'uncertain'
    monkeypatch.setattr(settings, 'public_site_url', 'https://changed.example')
    monkeypatch.setattr(settings, 'notification_destination_alias', 'changed.alias')
    relay.failure = None
    assert run(db_engine, relay, now + timedelta(seconds=6))
    assert relay.requests[1] == saved
    assert saved['request_id'] == feedback_id
    assert saved['button']['url'] == f'https://shop.example/#crm-feedback?case={feedback_id}'
    assert saved['destination_alias'] == 'mirzap67.staff'
    with Session(db_engine) as source:
        event = source.get(Outbox, feedback_id)
        event.payload = {key: value for key, value in event.payload.items() if key != 'relay_notification_id'}
        event.state, event.next_attempt_at = 'uncertain', now
        source.commit()
    assert run(db_engine, relay, now + timedelta(days=7, seconds=1))
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'rejected'
    assert len(relay.requests) == 2


def test_snapshot_is_committed_before_network_and_stale_worker_cannot_record_receipt(client, db_engine, db, relay_settings):
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    submit = relay.submit
    def inspect(snapshot):
        with Session(db_engine) as other:
            event = other.get(Outbox, feedback_id)
            assert event.payload['relay_request'] == snapshot
            event.attempts += 1
            event.lease_until = now + timedelta(seconds=120)
            other.commit()
        return submit(snapshot)
    relay.submit = inspect
    assert run(db_engine, relay, now)
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'running' and 'relay_notification_id' not in event.payload


@pytest.mark.parametrize('change', ['mode', 'target', 'credential', 'missing'])
def test_relay_snapshot_never_falls_back_to_direct_after_configuration_change(client, db_engine, db, relay_settings, monkeypatch, change):
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    assert run(db_engine, relay, now)
    monkeypatch.setattr(settings, 'telegram_bot_token', 'direct-token')
    monkeypatch.setattr(settings, 'telegram_chat_id', '-100000')
    if change == 'mode': monkeypatch.setattr(settings, 'notification_transport', 'direct')
    if change == 'target': monkeypatch.setattr(settings, 'notification_relay_target', 'other-relay.example:50051')
    if change == 'credential': monkeypatch.setattr(settings, 'notification_relay_token', 'other-secret')
    if change == 'missing': monkeypatch.setattr(settings, 'notification_relay_token', '')
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    if change == 'mode':
        assert relay.lookups == [relay.receipt.notification_id]
    else:
        assert event.last_error_code == 'RELAY_CONFIGURATION_CHANGED'
        assert not relay.lookups
    assert event.state != 'done'


def test_unavailable_lookup_preserves_uncertainty_and_never_resubmits(client, db_engine, db, relay_settings):
    from app.telegram_api import DeliveryError
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    assert run(db_engine, relay, now)
    relay.receipt.state, relay.receipt.duplicate_possible = pb.UNCERTAIN, True
    assert run(db_engine, relay, now + timedelta(seconds=6))
    relay.failure = DeliveryError('RELAY_UNAVAILABLE', uncertain=True)
    assert run(db_engine, relay, now + timedelta(seconds=37))
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'uncertain' and event.payload['relay_duplicate_possible']
    assert len(relay.requests) == 1


@pytest.mark.parametrize('failure', ['ca_read', 'lookup_capacity'])
def test_observation_failure_preserves_known_uncertainty_in_manager_projection(client, db_engine, db, manager_headers, relay_settings, monkeypatch, tmp_path, failure):
    from app.notification_worker import process_once
    from app.telegram_api import DeliveryError
    client.cookies.clear()
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    assert run(db_engine, relay, now)
    relay.receipt.state, relay.receipt.duplicate_possible = pb.UNCERTAIN, True
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    saved = event.payload
    assert event.state == 'uncertain' and saved['relay_duplicate_possible']
    path = f'/api/manager/feedback/{feedback_id}'
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'uncertain', 'code': None}
    if failure == 'ca_read':
        monkeypatch.setattr(settings, 'notification_relay_ca_file', str(tmp_path / 'missing-ca.pem'))
        monkeypatch.setattr(grpc, 'secure_channel', lambda *args, **kwargs: pytest.fail('Unreadable CA opened a connection'))
        assert process_once(session_factory=lambda: Session(db_engine), now=now + timedelta(seconds=37),
                            send=lambda _: pytest.fail('Uncertain relay event sent directly'))
        code = 'RELAY_TLS_CONFIGURATION_ERROR'
    else:
        relay.failure = DeliveryError('RELAY_RESOURCE_EXHAUSTED')
        assert run(db_engine, relay, now + timedelta(seconds=37))
        code = 'RELAY_RESOURCE_EXHAUSTED'
    db.expire_all()
    event = db.get(Outbox, feedback_id)
    assert event.state == 'uncertain' and event.last_error_code == code
    assert event.payload == saved
    assert event.next_attempt_at > now + timedelta(seconds=37) and event.lease_until is None
    assert len(relay.requests) == 1
    assert relay.lookups == [relay.receipt.notification_id] * (1 if failure == 'ca_read' else 2)
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'uncertain', 'code': code}


def test_failed_and_malformed_success_receipts_do_not_claim_delivery(client, db_engine, db, relay_settings):
    feedback_id, relay, now = queued_feedback(client), Relay(), utcnow()
    relay.receipt.state = pb.TELEGRAM_ACCEPTED
    assert run(db_engine, relay, now)
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'uncertain'
    relay.receipt.state = pb.FAILED
    relay.receipt.safe_error_code = 'DELIVERY_EXPIRED'
    assert run(db_engine, relay, now + timedelta(seconds=6))
    db.expire_all()
    assert db.get(Outbox, feedback_id).state == 'rejected'


def test_worker_ignores_registration_mail_and_erp(client, db_engine, db, relay_settings):
    for event_type in ('customer_verification_email', 'catalog_sync', 'order_export'):
        db.add(Outbox(id=str(uuid4()), event_type=event_type, payload={}, state='queued', attempts=0))
    db.commit()
    assert not run(db_engine, Relay(), utcnow())
    assert {event.state for event in db.query(Outbox)} == {'queued'}


def test_relay_readiness_does_not_require_bot_credentials(relay_settings):
    from app.notification_worker import is_configured
    assert is_configured()


def test_client_uses_tls_auth_and_bounded_deadlines(relay_settings, monkeypatch):
    from app.notification_relay_client import RelayClient
    from app.notification_protocol import notifications_pb2_grpc
    calls = []
    class Channel:
        def close(self): pass
    class Method:
        def __init__(self, name): self.name = name
        def __call__(self, request, **kwargs):
            calls.append((self.name, request, kwargs))
            return pb.NotificationReceipt(notification_id=request.notification_id if self.name == 'get' else str(uuid4()), state=pb.QUEUED)
    class Stub:
        def __init__(self, channel):
            self.SubmitNotification, self.GetNotification = Method('submit'), Method('get')
    def secure(target, credentials, **kwargs):
        assert target == 'relay.example:50051'
        assert credentials is not None
        return Channel()
    monkeypatch.setattr(grpc, 'secure_channel', secure)
    monkeypatch.setattr(notifications_pb2_grpc, 'NotificationRelayStub', Stub)
    client = RelayClient()
    snapshot = {'request_id': str(uuid4()), 'request_created_at': utcnow().isoformat(),
                'destination_alias': 'mirzap67.staff', 'text': 'Тест'}
    client.submit(snapshot)
    client.get(str(uuid4()))
    assert calls[0][2]['timeout'] == 5 and calls[1][2]['timeout'] == 3
    assert calls[0][2]['metadata'] == (('authorization', 'Bearer ' + settings.notification_relay_token),)


def test_feedback_projection_maps_relay_rejection_and_uses_relay_readiness(client, db, manager_headers, relay_settings):
    client.cookies.clear()
    feedback_id = queued_feedback(client)
    path = f'/api/manager/feedback/{feedback_id}'
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'queued', 'code': None}
    event = db.get(Outbox, feedback_id)
    event.state, event.last_error_code = 'rejected', 'DELIVERY_EXPIRED'
    db.commit()
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'error', 'code': 'DELIVERY_EXPIRED'}


def test_relay_technical_test_uses_selected_transport_without_creating_feedback(db_engine, db, relay_settings):
    from app.notification_worker import send_test_notification
    relay = Relay()
    result = send_test_notification(session_factory=lambda: Session(db_engine), relay_client_factory=lambda: relay,
                                    send=lambda _: pytest.fail('Technical test bypassed relay'))
    assert result.state == pb.QUEUED and not result.HasField('telegram_message_id')
    assert 'button' not in relay.requests[0] and 'техническое' in relay.requests[0]['text']
    assert db.query(Outbox).count() == 0


def test_relay_cli_never_probes_bot_api_and_reports_only_accepted(monkeypatch, capsys, relay_settings):
    import app.telegram_cli as cli
    import app.notification_relay_client as relay_client
    import app.notification_worker as worker
    relay = Relay()
    relay.check = lambda: True
    monkeypatch.setattr(cli, 'check_connection', lambda: pytest.fail('CLI bypassed relay'))
    monkeypatch.setattr(relay_client, 'RelayClient', lambda: relay)
    monkeypatch.setattr(worker, 'send_test_notification', lambda: relay.receipt)
    assert cli.main(['check']) == 0
    assert 'принято Telegram' not in capsys.readouterr().out
    assert cli.main(['test']) == 0
    output = capsys.readouterr().out
    assert 'relay' in output and 'принято Telegram' not in output


def test_relay_readiness_requires_destination_alias(relay_settings, monkeypatch):
    from app.notification_worker import is_configured
    monkeypatch.setattr(settings, 'notification_destination_alias', '')
    assert not is_configured()
