import argparse
import hashlib
import logging
import math
import random
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from .common import utcnow
from .config import settings, validate_notification_transport
from .db import SessionLocal
from .feedback import Feedback
from .integration import Outbox, TelegramDeliveryState
from .notifier import NotificationRequest, NotifierClient
from .notification_links import site_link
from .orders import Order
from .telegram_api import DeliveryError, send_telegram, site_origin

logger = logging.getLogger('lavr.notifications')
LEASE_SECONDS = 60


def is_configured(transport=None):
    try:
        transport = validate_notification_transport(transport)
    except DeliveryError:
        return False
    if transport == 'relay':
        from .notification_relay_client import relay_is_configured
        return bool(relay_is_configured() and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', settings.notification_destination_alias)
                    and site_origin() and site_origin().startswith('https://'))
    return bool(settings.telegram_bot_token and settings.telegram_chat_id and site_origin())


def notification_payload(subject):
    created = subject.created_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d-%m-%Y %H:%M')
    if isinstance(subject, Order):
        url = site_link('crm', {'order': subject.id})
        button = 'Открыть заказ'
        amount = f'{subject.total_kopecks // 100},{subject.total_kopecks % 100:02d} ₽'
        text = f'Новый заказ №{subject.number} от {created} МСК\nСумма: {amount} · Позиций: {len(subject.items)}'
    else:
        url = site_link('crm-feedback', {'case': subject.id})
        button = 'Открыть обращение'
        label = 'Свяжитесь со мной' if subject.type == 'contact' else 'Оставить запрос'
        text = f'Новое обращение №{subject.number} от {created}\n{label}'
    return {
        'chat_id': settings.telegram_chat_id,
        'text': text,
        'link_preview_options': {'is_disabled': True},
        'reply_markup': {'inline_keyboard': [[{
            'text': button,
            'url': url,
        }]]},
    }


def destination_key():
    # Keep bot credentials out of persistent delivery state and logs.
    return hashlib.sha256(f'{settings.telegram_bot_token}\0{settings.telegram_chat_id}'.encode()).hexdigest()


def remaining_pause(db, key, now):
    state = db.get(TelegramDeliveryState, key)
    return max(0, math.ceil((state.blocked_until - now).total_seconds())) if state else 0


def persist_pause(db, key, now, seconds):
    until = now + timedelta(seconds=max(1, min(seconds, 604800)))
    db.execute(insert(TelegramDeliveryState).values(destination_key=key, blocked_until=until).on_conflict_do_update(
        index_elements=[TelegramDeliveryState.destination_key],
        set_={'blocked_until': func.greatest(TelegramDeliveryState.blocked_until, until)},
    ))


def send_test_notification(*, session_factory=SessionLocal, send=send_telegram, now=None, relay_client_factory: Callable[[], NotifierClient] | None = None):
    if validate_notification_transport() == 'relay':
        from .common import new_id
        from .notification_relay_client import RelayClient, validate_receipt
        if not is_configured('relay'):
            raise DeliveryError('RELAY_NOT_CONFIGURED', retry_after=900)
        snapshot: NotificationRequest = {'request_id': new_id(), 'request_created_at': (now or utcnow()).isoformat(),
                    'destination_alias': settings.notification_destination_alias,
                    'text': 'Тест интеграции MIRZAP67. Это техническое сообщение, обращение не создаётся.'}
        client = (relay_client_factory or RelayClient)()
        try:
            return validate_receipt(client.submit(snapshot))
        finally:
            client.close()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise DeliveryError('TELEGRAM_NOT_CONFIGURED', retry_after=900)
    origin = site_origin()
    if settings.public_site_url and not origin:
        raise DeliveryError('TELEGRAM_INVALID_SITE_URL', retry_after=900)
    key, started = destination_key(), now or utcnow()
    with session_factory() as db:
        retry = remaining_pause(db, key, started)
    if retry:
        raise DeliveryError('TELEGRAM_RATE_LIMIT', retry_after=retry)
    payload = {
        'chat_id': settings.telegram_chat_id,
        'text': 'Тест интеграции MIRZAP67. Telegram-уведомления доступны. Это техническое сообщение, обращение не создаётся.',
        'link_preview_options': {'is_disabled': True},
    }
    if origin:
        payload['reply_markup'] = {'inline_keyboard': [[{'text': 'Открыть CRM', 'url': f'{origin}/#crm-feedback'}]]}
    try:
        return send(payload)
    except DeliveryError as error:
        if error.code == 'TELEGRAM_RATE_LIMIT':
            with session_factory() as db:
                persist_pause(db, key, now or utcnow(), error.retry_after)
                db.commit()
        raise


def source_fingerprint():
    return hashlib.sha256(settings.notification_relay_token.encode()).hexdigest()


def relay_snapshot(event_id, subject, now) -> NotificationRequest:
    payload = notification_payload(subject)
    button = payload['reply_markup']['inline_keyboard'][0][0]
    return {'request_id': event_id, 'request_created_at': now.isoformat(),
            'destination_alias': settings.notification_destination_alias, 'text': payload['text'],
            'button': {'label': button['text'], 'url': button['url']}}


def process_once(*, session_factory=SessionLocal, send=send_telegram, now=None, relay_client_factory: Callable[[], NotifierClient] | None = None, orders_only=False):
    if not settings.telegram_notifications_enabled:
        return False
    try:
        validate_notification_transport()
    except DeliveryError:
        return False
    claimed_at = now or utcnow()
    with session_factory() as db:
        event = db.scalar(select(Outbox).where(
            Outbox.event_type.in_(('order_telegram',) if orders_only else ('feedback_telegram', 'order_telegram')),
            or_(
                and_(Outbox.state.in_(('queued', 'error', 'uncertain')),
                     or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= claimed_at)),
                and_(Outbox.state == 'running', Outbox.lease_until <= claimed_at),
            ),
        ).order_by(Outbox.created_at, Outbox.id).limit(1).with_for_update(skip_locked=True))
        if event is None:
            return False
        saved = event.payload
        was_uncertain = event.state == 'uncertain' or bool(saved.get('relay_duplicate_possible'))
        use_relay = bool(saved.get('relay_request')) or settings.notification_transport == 'relay'
        if not saved.get('relay_request') and not is_configured('relay' if use_relay else 'direct'):
            return False
        key = None if use_relay else destination_key()
        if key and remaining_pause(db, key, claimed_at):
            return False
        event.state = 'running'
        event.attempts += 1
        event.lease_until = claimed_at + timedelta(seconds=LEASE_SECONDS)
        event_id, attempt, lease = event.id, event.attempts, event.lease_until
        if event.event_type == 'order_telegram':
            subject = db.get(Order, event.order_id) if event.order_id else None
            missing_code = 'ORDER_NOT_FOUND'
        else:
            subject = db.get(Feedback, saved.get('feedbackId'))
            missing_code = 'FEEDBACK_NOT_FOUND'
        payload = notification_payload(subject) if subject and not use_relay else None
        if use_relay and not saved.get('relay_request') and subject:
            saved = {**saved, 'relay_request': relay_snapshot(event_id, subject, claimed_at),
                     'relay_target': settings.notification_relay_target, 'relay_source_fingerprint': source_fingerprint()}
            event.payload = saved
        db.commit()

    failure, remote, message_id = None, None, None
    try:
        if use_relay:
            from .notification_relay_client import RelayClient, validate_receipt
            if not saved.get('relay_request'):
                raise DeliveryError(missing_code, retry_after=900)
            if saved.get('relay_target') != settings.notification_relay_target or saved.get('relay_source_fingerprint') != source_fingerprint() or not settings.notification_relay_token:
                raise DeliveryError('RELAY_CONFIGURATION_CHANGED', retry_after=900, uncertain=True)
            known_id = saved.get('relay_notification_id')
            created = datetime.fromisoformat(saved['relay_request']['request_created_at'])
            if not known_id and claimed_at >= created + timedelta(days=7):
                raise DeliveryError('REQUEST_EXPIRED')
            client = (relay_client_factory or RelayClient)()
            try:
                remote = validate_receipt(client.get(known_id) if known_id else client.submit(saved['relay_request']), known_id)
            finally:
                client.close()
        else:
            if payload is None:
                raise DeliveryError(missing_code, retry_after=900)
            message_id = send(payload)
            if type(message_id) is not int or message_id <= 0:
                message_id = None
                raise DeliveryError('TELEGRAM_INVALID_RESPONSE', uncertain=True)
    except DeliveryError as error:
        failure = error
    except Exception:
        failure = DeliveryError('RELAY_UNAVAILABLE' if use_relay else 'TELEGRAM_NETWORK_ERROR', uncertain=True)

    finished_at = now or utcnow()
    state, next_at, code = 'done', None, None
    if remote is not None:
        from .notification_protocol import notifications_pb2 as pb
        states = {pb.QUEUED: 'queued', pb.RETRY_WAIT: 'queued', pb.SENDING: 'running',
                  pb.UNCERTAIN: 'uncertain', pb.TELEGRAM_ACCEPTED: 'done', pb.FAILED: 'rejected'}
        state = states[remote.state]
        code = DeliveryError(remote.safe_error_code).code if remote.safe_error_code else None
        if state not in ('done', 'rejected'):
            delay = 5
            if remote.HasField('next_attempt_at'):
                delay = max(5, min(900, remote.next_attempt_at.ToDatetime(tzinfo=finished_at.tzinfo).timestamp() - finished_at.timestamp()))
            next_at = finished_at + timedelta(seconds=delay)
        if remote.state == pb.TELEGRAM_ACCEPTED:
            message_id = remote.telegram_message_id
    if failure:
        terminal = failure.code in ('RELAY_REQUEST_INVALID', 'RELAY_UNAUTHENTICATED', 'RELAY_PERMISSION_DENIED',
                                   'RELAY_IDEMPOTENCY_CONFLICT', 'RELAY_NOT_FOUND', 'REQUEST_EXPIRED', 'ORDER_NOT_FOUND')
        delay = (5, 30, 120, 900)[min(attempt - 1, 3)] + (random.uniform(0, 1) if use_relay else 0)
        uncertain = failure.uncertain or (use_relay and was_uncertain)
        state = 'rejected' if terminal else ('uncertain' if uncertain else 'error')
        next_at = None if terminal else finished_at + timedelta(seconds=max(delay, failure.retry_after))
        code = failure.code
    with session_factory() as db:
        event = db.scalar(select(Outbox).where(
            Outbox.id == event_id, Outbox.state == 'running', Outbox.attempts == attempt,
            Outbox.lease_until == lease,
        ).with_for_update())
        if event is None:
            return True
        if failure and failure.code == 'TELEGRAM_RATE_LIMIT' and key:
            persist_pause(db, key, finished_at, failure.retry_after)
        payload = event.payload
        if remote is not None:
            payload = {**payload, 'relay_checked_at': finished_at.isoformat(), 'relay_notification_id': remote.notification_id,
                       'relay_duplicate_possible': bool(payload.get('relay_duplicate_possible') or remote.duplicate_possible)}
        if message_id is not None:
            payload = {**payload, 'telegram_message_id': message_id}
        event.payload = payload
        event.state, event.next_attempt_at, event.last_error_code = state, next_at, code
        # Remote SENDING is a pollable status, rather than a held source lease.
        event.lease_until = next_at if state == 'running' else None
        db.commit()
        if failure:
            logger.warning('Notification %s: %s', event_id, failure.code)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Deliver queued Telegram notifications')
    parser.add_argument('--orders-only', action='store_true', help='Process only order notifications')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    previous_state = None
    while True:
        state = 'paused' if not settings.telegram_notifications_enabled else ('configured' if is_configured() else 'awaiting configuration')
        if state != previous_state:
            logger.info('Telegram notifications %s', state)
            previous_state = state
        try:
            processed = process_once(orders_only=args.orders_only)
        except Exception:
            logger.error('Notification queue: DATABASE_OR_WORKER_ERROR')
            processed = False
        time.sleep(1 if processed else 5)


if __name__ == '__main__':
    main()
