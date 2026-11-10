"""The vendored V1 SDK stays usable without the standalone server package."""
import hashlib
from pathlib import Path

from app.notification_protocol import notifications_pb2 as pb
from app.notifier import NotificationRequest, NotifierClient


ROOT = Path(__file__).resolve().parents[2]


def test_crm_runtime_contains_only_the_notifier_client():
    assert not (ROOT / 'backend/app/telegram_relay.py').exists()
    assert not (ROOT / 'backend/app/notification_ledger.py').exists()
    assert set(NotificationRequest.__required_keys__) == {
        'request_id', 'request_created_at', 'destination_alias', 'text'}
    assert NotificationRequest.__optional_keys__ == {'button'}
    assert all(callable(getattr(NotifierClient, name)) for name in ('submit', 'get', 'close'))


def test_v1_proto_and_navigation_fixture_remain_identical_to_handoff():
    expected = {
        'backend/app/notification_protocol/notifications.proto':
            '3bbcf071785703643850561b3b6f996e97f26d1ad1eca27290529d909c663daf',
        'tests/fixtures/navigation-v1.json':
            'f2ff95c647e946a35923dfce7ea42c7cbf7416e03b9523ec97f4cc47ec144df8',
    }
    for filename, digest in expected.items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest


def test_v1_wire_names_methods_fields_enums_and_optional_receipt_id():
    service = pb.DESCRIPTOR.services_by_name['NotificationRelay']
    assert service.full_name == 'notifications.v1.NotificationRelay'
    assert [(method.name, method.input_type.full_name, method.output_type.full_name)
            for method in service.methods] == [
        ('SubmitNotification', 'notifications.v1.SubmitNotificationRequest', 'notifications.v1.NotificationReceipt'),
        ('GetNotification', 'notifications.v1.GetNotificationRequest', 'notifications.v1.NotificationReceipt'),
    ]
    fields = {
        pb.SubmitNotificationRequest: ['request_id', 'request_created_at', 'destination_alias', 'text', 'button'],
        pb.Button: ['label', 'url'],
        pb.GetNotificationRequest: ['notification_id'],
        pb.NotificationReceipt: ['notification_id', 'state', 'accepted_at', 'updated_at',
                                 'next_attempt_at', 'safe_error_code', 'telegram_message_id', 'duplicate_possible'],
    }
    for message, names in fields.items():
        assert [(field.name, field.number) for field in message.DESCRIPTOR.fields] == [
            (name, index) for index, name in enumerate(names, 1)]
    assert list(pb.NotificationState.items()) == [
        ('NOTIFICATION_STATE_UNSPECIFIED', 0), ('QUEUED', 1), ('SENDING', 2),
        ('RETRY_WAIT', 3), ('UNCERTAIN', 4), ('TELEGRAM_ACCEPTED', 5), ('FAILED', 6),
    ]
    receipt = pb.NotificationReceipt.FromString(b'\x0a\x01x\x10\x05\x38\x07\x40\x01')
    assert receipt.notification_id == 'x' and receipt.state == pb.TELEGRAM_ACCEPTED
    assert receipt.HasField('telegram_message_id') and receipt.telegram_message_id == 7
    assert receipt.duplicate_possible
    assert not pb.NotificationReceipt().HasField('telegram_message_id')
