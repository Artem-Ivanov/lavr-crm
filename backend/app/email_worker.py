"""Independent, durable verification email delivery over authenticated TLS."""
import hmac
import logging
import re
import smtplib
import ssl
import time
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import and_, delete, null, or_, select, update

from .accounts import FLOW_LIFETIME, ChallengeThrottle, UserChallenge, code_digest
from .auth import User
from .common import utcnow
from .config import settings
from .crypto import seal_json, validate_configuration
from .db import SessionLocal
from .integration import Outbox

logger = logging.getLogger('lavr.email')
EVENT_TYPE = 'customer_verification_email'
LEASE_SECONDS = 60
MAX_ATTEMPTS = 5


class EmailDeliveryError(Exception):
    pass


def smtp_configured():
    return bool(settings.smtp_host and not re.search(r'[\s\x00-\x1f\x7f]', settings.smtp_host)
                and 1 <= settings.smtp_port <= 65535 and settings.smtp_tls_mode in ('tls', 'starttls')
                and re.fullmatch(r'[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+', settings.smtp_from)
                and bool(settings.smtp_username) == bool(settings.smtp_password))


def send_email(payload):
    if not smtp_configured():
        raise EmailDeliveryError('SMTP_NOT_CONFIGURED')
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = settings.smtp_from, payload['to'], payload['subject']
    message.set_content(payload['text'])
    context = ssl.create_default_context()
    connection = smtplib.SMTP_SSL if settings.smtp_tls_mode == 'tls' else smtplib.SMTP
    options = {'timeout': 30}
    if settings.smtp_tls_mode == 'tls':
        options['context'] = context
    try:
        with connection(settings.smtp_host, settings.smtp_port, **options) as smtp:
            if settings.smtp_tls_mode == 'starttls':
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            if smtp.send_message(message):
                raise EmailDeliveryError('SMTP_DELIVERY_ERROR')
    except Exception:
        raise EmailDeliveryError('SMTP_DELIVERY_ERROR') from None


def cleanup(db, now):
    db.execute(update(UserChallenge).where(
        or_(UserChallenge.created_at <= now - FLOW_LIFETIME, UserChallenge.consumed_at.is_not(None)),
        or_(UserChallenge.encrypted_payload.is_not(None), UserChallenge.code_hmac.is_not(None)),
    ).values(encrypted_payload=null(), code_hmac=None))
    db.execute(delete(UserChallenge).where(UserChallenge.created_at <= now - FLOW_LIFETIME - timedelta(days=1)))
    db.execute(delete(ChallengeThrottle).where(ChallengeThrottle.window_start <= now - timedelta(hours=1)))
    for event in db.scalars(select(Outbox).where(Outbox.event_type == EVENT_TYPE,
                                               Outbox.state.not_in(['done', 'cancelled', 'rejected']),
                                               Outbox.created_at <= now - timedelta(minutes=10)).with_for_update(skip_locked=True)):
        event.state, event.lease_until, event.next_attempt_at = 'cancelled', None, None
        event.payload = {}


def _live(flow, payload, now, db):
    if not (flow and flow.encrypted_payload is not None and flow.code_hmac and flow.consumed_at is None
            and flow.created_at + FLOW_LIFETIME > now and flow.expires_at > now and flow.attempts < 5
            and flow.generation == payload.get('generation')):
        return False
    if not hmac.compare_digest(flow.code_hmac, code_digest(flow, payload.get('code', ''))):
        return False
    pending = flow.payload
    if pending['email'] != payload.get('to'):
        return False
    if pending.get('user_id'):
        user = db.get(User, pending['user_id'])
        if not user or not user.is_active or user.auth_version != pending['auth_version']:
            return False
    return True


def process_once(*, session_factory=SessionLocal, send=send_email, now=None):
    started = now or utcnow()
    with session_factory() as db:
        cleanup(db, started)
        db.commit()
        if not smtp_configured():
            return False
        event = db.scalar(select(Outbox).where(
            Outbox.event_type == EVENT_TYPE,
            or_(Outbox.state.in_(['queued', 'error']), and_(Outbox.state == 'running', Outbox.lease_until <= started)),
            or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= started),
        ).order_by(Outbox.created_at, Outbox.id).with_for_update(skip_locked=True).limit(1))
        if event is None:
            return False
        if event.attempts >= MAX_ATTEMPTS:
            event.state, event.lease_until, event.next_attempt_at = 'rejected', None, None
            event.last_error_code, event.payload = 'SMTP_ATTEMPTS_EXHAUSTED', {}
            db.commit()
            return True
        event.attempts += 1
        event.state, event.lease_until = 'running', started + timedelta(seconds=LEASE_SECONDS)
        event_id, attempt, lease = event.id, event.attempts, event.lease_until
        try:
            payload = event.payload
        except Exception:
            payload = {}
        db.commit()

    failure = None
    with session_factory() as db:
        flow = db.scalar(select(UserChallenge).where(UserChallenge.flow_id == payload.get('flowId')).with_for_update())
        event = db.get(Outbox, event_id)
        if not event or event.state != 'running' or event.attempts != attempt or event.lease_until != lease:
            return True
        try:
            live = _live(flow, payload, now or utcnow(), db)
        except Exception:
            live = False
        if not live:
            values = {'state': 'cancelled', 'lease_until': None, 'next_attempt_at': None, 'last_error_code': None}
        else:
            # Hold the flow lock through SMTP so a resend/consume cannot invalidate the code mid-send.
            try:
                send(payload)
            except Exception:
                failure = 'SMTP_DELIVERY_ERROR'
            values = {'state': 'done', 'lease_until': None, 'next_attempt_at': None, 'last_error_code': None}
            if failure:
                values.update(state='error' if attempt < MAX_ATTEMPTS else 'rejected', last_error_code=failure,
                              next_attempt_at=(now or utcnow()) + timedelta(seconds=(5, 30, 120, 300)[min(attempt - 1, 3)]) if attempt < MAX_ATTEMPTS else None)
        if values['state'] in ('done', 'cancelled', 'rejected'):
            values['payload'] = seal_json({}, table='outbox', record_id=event_id, field='payload')
        changed = db.execute(update(Outbox).where(Outbox.id == event_id, Outbox.state == 'running',
                                                 Outbox.attempts == attempt, Outbox.lease_until == lease).values(**values).execution_options(synchronize_session=False))
        db.commit()
        if failure and changed.rowcount:
            logger.warning('Email %s: SMTP_DELIVERY_ERROR', event_id)
    return True


def main():
    validate_configuration()
    if len(settings.challenge_secret) < 32:
        raise RuntimeError('CHALLENGE_SECRET must contain at least 32 characters')
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            processed = process_once()
        except Exception:
            logger.error('Email queue: DATABASE_OR_WORKER_ERROR')
            processed = False
        time.sleep(1 if processed else 5)


if __name__ == '__main__':
    main()
