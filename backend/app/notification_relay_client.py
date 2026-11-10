"""Bounded TLS-only source client; transport failures reveal no remote details."""
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import grpc

from .config import settings
from .notifier import NotificationRequest
from .notification_protocol import notifications_pb2 as pb, notifications_pb2_grpc as rpc
from .telegram_api import DeliveryError


def relay_is_configured():
    target, token = settings.notification_relay_target, settings.notification_relay_token
    match = re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):([0-9]{1,5})', target)
    return bool(match and 1 <= int(match[1]) <= 65535 and 32 <= len(token) <= 1024 and not any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in token))


def validate_receipt(receipt, expected_id=None):
    from uuid import UUID
    try:
        UUID(receipt.notification_id)
        if expected_id is not None and receipt.notification_id != expected_id:
            raise ValueError
        if receipt.state not in (pb.QUEUED, pb.SENDING, pb.RETRY_WAIT, pb.UNCERTAIN, pb.TELEGRAM_ACCEPTED, pb.FAILED):
            raise ValueError
        if receipt.state == pb.TELEGRAM_ACCEPTED:
            if not receipt.HasField('telegram_message_id') or receipt.telegram_message_id <= 0:
                raise ValueError
        elif receipt.HasField('telegram_message_id'):
            raise ValueError
        for field in ('accepted_at', 'updated_at', 'next_attempt_at'):
            if receipt.HasField(field): getattr(receipt, field).ToDatetime()
    except (ValueError, AttributeError, OverflowError):
        raise DeliveryError('RELAY_INVALID_RESPONSE', uncertain=True) from None
    return receipt


class RelayClient:
    def __init__(self):
        if not relay_is_configured():
            raise DeliveryError('RELAY_NOT_CONFIGURED', retry_after=900)
        try:
            roots = Path(settings.notification_relay_ca_file).read_bytes() if settings.notification_relay_ca_file else None
            credentials = grpc.ssl_channel_credentials(root_certificates=roots)
            self.channel = grpc.secure_channel(settings.notification_relay_target, credentials,
                options=(('grpc.max_receive_message_length', 32768), ('grpc.max_send_message_length', 32768), ('grpc.enable_retries', 0)))
            self.stub = rpc.NotificationRelayStub(self.channel)
        except (OSError, ValueError):
            raise DeliveryError('RELAY_TLS_CONFIGURATION_ERROR', retry_after=900) from None
        self.metadata = (('authorization', 'Bearer ' + settings.notification_relay_token),)

    def close(self) -> None:
        self.channel.close()

    def _call(self, method, request, deadline):
        try:
            return method(request, timeout=deadline, metadata=self.metadata)
        except grpc.RpcError as error:
            codes = {grpc.StatusCode.INVALID_ARGUMENT: 'RELAY_REQUEST_INVALID', grpc.StatusCode.UNAUTHENTICATED: 'RELAY_UNAUTHENTICATED',
                     grpc.StatusCode.PERMISSION_DENIED: 'RELAY_PERMISSION_DENIED', grpc.StatusCode.ALREADY_EXISTS: 'RELAY_IDEMPOTENCY_CONFLICT',
                     grpc.StatusCode.NOT_FOUND: 'RELAY_NOT_FOUND', grpc.StatusCode.RESOURCE_EXHAUSTED: 'RELAY_RESOURCE_EXHAUSTED',
                     grpc.StatusCode.FAILED_PRECONDITION: 'REQUEST_EXPIRED', grpc.StatusCode.DEADLINE_EXCEEDED: 'RELAY_DEADLINE_EXCEEDED'}
            code = codes.get(error.code(), 'RELAY_UNAVAILABLE')
            raise DeliveryError(code, uncertain=code in ('RELAY_UNAVAILABLE', 'RELAY_DEADLINE_EXCEEDED')) from None

    def submit(self, snapshot: NotificationRequest) -> pb.NotificationReceipt:
        try:
            request = pb.SubmitNotificationRequest(request_id=snapshot['request_id'], destination_alias=snapshot['destination_alias'], text=snapshot['text'])
            request.request_created_at.FromDatetime(datetime.fromisoformat(snapshot['request_created_at']))
            if snapshot.get('button'):
                request.button.label, request.button.url = snapshot['button']['label'], snapshot['button']['url']
        except (KeyError, TypeError, ValueError, OverflowError):
            raise DeliveryError('RELAY_REQUEST_INVALID') from None
        return validate_receipt(self._call(self.stub.SubmitNotification, request, 5))

    def get(self, notification_id: str) -> pb.NotificationReceipt:
        return validate_receipt(self._call(self.stub.GetNotification, pb.GetNotificationRequest(notification_id=notification_id), 3), notification_id)

    def check(self):
        try:
            self.get(str(uuid4()))
        except DeliveryError as error:
            if error.code == 'RELAY_NOT_FOUND': return True
            raise
        return True
