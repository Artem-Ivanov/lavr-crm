"""External notifier client boundary; receipts use the existing wire contract."""
from typing import NotRequired, Protocol, TypedDict

from .notification_protocol.notifications_pb2 import NotificationReceipt


class NotificationButton(TypedDict):
    label: str
    url: str


class NotificationRequest(TypedDict):
    request_id: str
    request_created_at: str
    destination_alias: str
    text: str
    button: NotRequired[NotificationButton]


class NotifierClient(Protocol):
    def submit(self, request: NotificationRequest, /) -> NotificationReceipt: ...
    def get(self, notification_id: str) -> NotificationReceipt: ...
    def close(self) -> None: ...
