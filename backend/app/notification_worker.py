import hashlib
import logging
import math
import time
from datetime import timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from .common import utcnow
from .config import settings
from .db import SessionLocal
from .feedback import Feedback
from .integration import Outbox, TelegramDeliveryState
from .telegram_api import DeliveryError, send_telegram, site_origin

logger = logging.getLogger('lavr.notifications')
LEASE_SECONDS = 60


def is_configured():
    return bool(settings.telegram_bot_token and settings.telegram_chat_id and site_origin())


def notification_payload(feedback):
    origin = site_origin()
    if not origin:
        raise DeliveryError('TELEGRAM_NOT_CONFIGURED', retry_after=900)
    label = 'Свяжитесь со мной' if feedback.type == 'contact' else 'Оставить запрос'
    created = feedback.created_at.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')
    return {
        'chat_id': settings.telegram_chat_id,
        'text': f'Новое обращение № {feedback.number}\n{created}\n{label}',
        'link_preview_options': {'is_disabled': True},
        'reply_markup': {'inline_keyboard': [[{
            'text': 'Открыть обращение',
            'url': f'{origin}/#crm-feedback?case={quote(feedback.id, safe="")}',
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


def send_test_notification(*, session_factory=SessionLocal, send=send_telegram, now=None):
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
        'text': 'Тест интеграции LAVR. Telegram-уведомления доступны. Это техническое сообщение, обращение не создаётся.',
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


def process_once(*, session_factory=SessionLocal, send=send_telegram, now=None):
    if not settings.telegram_notifications_enabled or not is_configured():
        return False
    claimed_at = now or utcnow()
    key = destination_key()
    with session_factory() as db:
        if remaining_pause(db, key, claimed_at):
            return False
        event = db.scalar(select(Outbox).where(
            Outbox.event_type == 'feedback_telegram',
            or_(
                and_(Outbox.state.in_(('queued', 'error', 'uncertain')),
                     or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= claimed_at)),
                and_(Outbox.state == 'running', Outbox.lease_until <= claimed_at),
            ),
        ).order_by(Outbox.created_at, Outbox.id).limit(1).with_for_update(skip_locked=True))
        if event is None:
            return False
        event.state = 'running'
        event.attempts += 1
        event.lease_until = claimed_at + timedelta(seconds=LEASE_SECONDS)
        event_id, attempt, lease = event.id, event.attempts, event.lease_until
        feedback = db.get(Feedback, event.payload.get('feedbackId'))
        payload = notification_payload(feedback) if feedback else None
        db.commit()

    failure = None
    try:
        if payload is None:
            raise DeliveryError('FEEDBACK_NOT_FOUND', retry_after=900)
        send(payload)
    except DeliveryError as error:
        failure = error
    except Exception:
        failure = DeliveryError('TELEGRAM_NETWORK_ERROR', uncertain=True)

    finished_at = now or utcnow()
    values = {'lease_until': None, 'state': 'done', 'next_attempt_at': None, 'last_error_code': None}
    if failure:
        delay = (5, 30, 120, 900)[min(attempt - 1, 3)]
        values.update(state='uncertain' if failure.uncertain else 'error',
                      next_attempt_at=finished_at + timedelta(seconds=max(delay, failure.retry_after)),
                      last_error_code=failure.code)
    with session_factory() as db:
        if failure and failure.code == 'TELEGRAM_RATE_LIMIT':
            persist_pause(db, key, finished_at, failure.retry_after)
        changed = db.execute(update(Outbox).where(
            Outbox.id == event_id, Outbox.state == 'running',
            Outbox.attempts == attempt, Outbox.lease_until == lease,
        ).values(**values))
        db.commit()
        if failure and changed.rowcount:
            logger.warning('Notification %s: %s', event_id, failure.code)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    previous_state = None
    while True:
        state = 'paused' if not settings.telegram_notifications_enabled else ('configured' if is_configured() else 'awaiting configuration')
        if state != previous_state:
            logger.info('Telegram notifications %s', state)
            previous_state = state
        try:
            processed = process_once()
        except Exception:
            logger.error('Notification queue: DATABASE_OR_WORKER_ERROR')
            processed = False
        time.sleep(1 if processed else 5)


if __name__ == '__main__':
    main()
