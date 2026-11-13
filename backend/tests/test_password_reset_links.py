from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import re
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import UserChallenge, code_digest
from app.auth import User, UserSession, password_hasher
from app.common import utcnow
from app.config import settings
from app.integration import Outbox
from test_accounts import deliver, mail_settings, signup


def begin_reset(client, db_engine):
    response = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'})
    assert response.status_code == 202, response.text
    sent = deliver(db_engine)
    assert len(sent) == 1
    return response.json()['flowId'], sent[0]['code'], sent[0]


def reset_body(flow, token, password='updated-password-123'):
    return {'flowId': flow, 'token': token, 'newPassword': password, 'confirmPassword': password}


def fresh_confirm(client, body):
    with TestClient(client.app, headers={'Origin': 'http://testserver'}) as fresh:
        assert not fresh.cookies
        return fresh.post('/api/customer/auth/password-reset/confirm', json=body)


def test_reset_email_contains_only_configured_https_link_and_redemption_works_without_cookie(client, db_engine, db, mail_settings, partner_headers):
    response = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}, headers={'Host': 'attacker.example'})
    assert response.status_code == 202
    assert set(response.json()) == {'flowId', 'resendAfterSeconds'}
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    payload = event.payload
    token = payload['code']
    assert bool(re.fullmatch(r'[A-Za-z0-9_-]{43}', token))
    match = re.search(r'https://\S+', payload['text'])
    assert match is not None
    link = urlsplit(match.group())
    assert (link.scheme, link.netloc, link.path, link.query) == ('https', 'shop.example', '/', '')
    route, query = link.fragment.split('?', 1)
    assert route == 'password-reset'
    parameters = parse_qs(query)
    assert set(parameters) == {'flow', 'token'}
    assert parameters['flow'] == [response.json()['flowId']]
    assert parameters['token'] == [token]
    assert 'Восстановление пароля' in payload['subject']
    assert 'test-password-123' not in payload['text']
    raw = json.dumps([dict(row) for row in db.execute(select(Outbox.__table__)).mappings()], default=str)
    raw += json.dumps([dict(row) for row in db.execute(select(UserChallenge.__table__)).mappings()], default=str)
    assert token not in raw and match.group() not in raw and 'buyer@example.test' not in raw
    flow = db.get(UserChallenge, response.json()['flowId'])
    assert flow.code_hmac != token and flow.code_hmac == code_digest(flow, token)
    assert flow.expires_at == flow.created_at + timedelta(minutes=10)
    sent = deliver(db_engine)
    assert len(sent) == 1
    db.refresh(event)
    assert event.state == 'done' and event.payload == {}
    before = db.get(User, 'partner').password_hash
    with TestClient(client.app, headers={'Origin': 'http://testserver'}) as fresh:
        assert fresh.get('/api/customer/auth/password-reset/confirm', params=parameters).status_code == 405
        db.refresh(flow)
        assert flow.consumed_at is None and flow.attempts == 0
        response = fresh.post('/api/customer/auth/password-reset/confirm', json=reset_body(flow.flow_id, token))
    assert response.status_code == 204, response.text
    db.expire_all()
    user, flow = db.get(User, 'partner'), db.get(UserChallenge, flow.flow_id)
    assert user.password_hash != before and password_hasher.verify(user.password_hash, 'updated-password-123')
    assert flow.consumed_at and flow.code_hmac is None and flow.encrypted_payload is None
    assert fresh_confirm(client, reset_body(flow.flow_id, token)).status_code == 400


@pytest.mark.parametrize('changes', [
    {'confirmPassword': 'different-password-123'}, {'confirmPassword': 'short'}, {'newPassword': 'short'},
    {'newPassword': 'x' * 257}, {'confirmPassword': 'x' * 257}, {'newPassword': 'password-123\x00'},
    {'confirmPassword': 'password-123\x00'}, {'token': '123456'}, {'token': 'a' * 42}, {'token': '/' * 43},
    {'code': '123456'},
])
def test_reset_rejects_invalid_or_mismatched_fields_before_hashing(client, db_engine, db, mail_settings, partner_headers, monkeypatch, changes):
    import app.accounts as accounts

    flow, token, _ = begin_reset(client, db_engine)
    user = db.get(User, 'partner')
    original = user.password_hash, user.auth_version
    def forbidden(_):
        pytest.fail('Invalid password reset reached hashing')
    monkeypatch.setattr(accounts, 'hash_password', forbidden)
    response = fresh_confirm(client, {**reset_body(flow, token), **changes})
    assert response.status_code == 422
    assert token not in response.text and 'updated-password-123' not in response.text
    db.refresh(user)
    assert (user.password_hash, user.auth_version) == original
    challenge = db.get(UserChallenge, flow)
    assert challenge.consumed_at is None and challenge.attempts == 0


@pytest.mark.parametrize('state', ['expired', 'tampered', 'wrong_purpose', 'unknown_flow'])
def test_reset_link_cannot_authorize_invalid_challenges(client, db_engine, db, mail_settings, partner_headers, monkeypatch, state):
    import app.accounts as accounts

    flow, token, _ = begin_reset(client, db_engine)
    original = db.get(User, 'partner').password_hash
    if state == 'expired':
        monkeypatch.setattr(accounts, 'utcnow', lambda: db.get(UserChallenge, flow).expires_at)
    elif state == 'tampered':
        token = ('a' if token[0] != 'a' else 'b') + token[1:]
    elif state == 'wrong_purpose':
        other = signup(client).json()['flowId']
        challenge = db.get(UserChallenge, other)
        challenge.code_hmac = code_digest(challenge, token)
        db.commit()
        flow = other
    else:
        flow = '00000000-0000-0000-0000-000000000000'
    response = fresh_confirm(client, reset_body(flow, token))
    assert response.status_code == 400
    assert response.json()['message'] == 'Ссылка недействительна или срок действия истёк. Запросите новую ссылку.'
    db.expire_all()
    assert db.get(User, 'partner').password_hash == original


def test_reset_wrong_tokens_keep_attempt_limit_and_resend_invalidates_old_link(client, db_engine, db, mail_settings, partner_headers, monkeypatch):
    import app.accounts as accounts

    flow = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}).json()['flowId']
    old_event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    token = old_event.payload['code']
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(accounts, 'utcnow', lambda: later)
    assert client.post('/api/customer/auth/resend', json={'flowId': flow}).status_code == 202
    sent = deliver(db_engine)
    assert len(sent) == 1 and sent[0]['code'] != token
    db.refresh(old_event)
    assert old_event.state == 'cancelled' and old_event.payload == {}
    assert fresh_confirm(client, reset_body(flow, token)).status_code == 400
    wrong = 'a' * 43 if sent[0]['code'] != 'a' * 43 else 'b' * 43
    for _ in range(4):
        assert fresh_confirm(client, reset_body(flow, wrong)).status_code == 400
    assert fresh_confirm(client, reset_body(flow, sent[0]['code'])).status_code == 429


@pytest.mark.parametrize('same_flow', [True, False])
def test_concurrent_reset_links_change_password_only_once(client, db_engine, db, mail_settings, partner_headers, same_flow):
    first = begin_reset(client, db_engine)[:2]
    second = first if same_flow else begin_reset(client, db_engine)[:2]
    gate = Barrier(2)
    def confirm(index):
        flow, token = (first, second)[index]
        with TestClient(client.app, headers={'Origin': 'http://testserver'}) as fresh:
            gate.wait()
            response = fresh.post('/api/customer/auth/password-reset/confirm', json=reset_body(flow, token, f'winner-password-{index}'))
            return index, response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert sorted(status for _, status in results) == [204, 400]
    db.expire_all()
    user = db.get(User, 'partner')
    winner = next(index for index, status in results if status == 204)
    assert user.auth_version == 2 and password_hasher.verify(user.password_hash, f'winner-password-{winner}')
    assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user.id)) == 0


@pytest.mark.parametrize('state', ['inactive', 'revoked', 'email_changed', 'unverified'])
@pytest.mark.parametrize('before_delivery', [True, False])
def test_reset_tracks_current_account_before_delivery_and_redemption(client, db_engine, db, mail_settings, partner_headers, state, before_delivery):
    from app.email_worker import process_once

    response = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'})
    flow = response.json()['flowId']
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    token = event.payload['code']
    if not before_delivery:
        assert len(deliver(db_engine)) == 1
    user = db.get(User, 'partner')
    original = user.password_hash
    if state == 'inactive':
        user.is_active = False
    elif state == 'revoked':
        user.auth_version += 1
    elif state == 'email_changed':
        user.profile = {'email': 'changed@example.test', 'phone': '+79991234567'}
    else:
        user.email_verified_at = None
    db.commit()
    if before_delivery:
        assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Stale reset link sent'))
        db.refresh(event)
        assert event.state == 'cancelled' and event.payload == {}
    assert fresh_confirm(client, reset_body(flow, token)).status_code == 400
    db.refresh(user)
    assert user.password_hash == original


@pytest.mark.parametrize('origin', ['', 'http://shop.example', 'https://name:password@shop.example', 'https://shop.example/path',
                                   'https://shop.example?redirect=elsewhere', 'https://shop.example/#other', 'https://shop.example\\evil',
                                   'https://shop.example\n', 'https://shop.example:bad'])
def test_reset_requires_safe_origin_without_disclosing_account_existence(client, db, mail_settings, partner_headers, monkeypatch, origin):
    monkeypatch.setattr(settings, 'public_site_url', origin)
    for email in ('buyer@example.test', 'unknown@example.test'):
        response = client.post('/api/customer/auth/password-reset', json={'email': email})
        assert response.status_code == 503 and response.json()['code'] == 'EMAIL_NOT_CONFIGURED'
    assert db.scalars(select(Outbox)).all() == []
    assert signup(client).status_code == 202


def test_reset_request_is_generic_for_unknown_blocked_and_unverified_accounts(client, db_engine, db, mail_settings, partner_headers):
    for state in ('eligible', 'unknown', 'blocked', 'unverified'):
        user = db.get(User, 'partner')
        user.is_active = state != 'blocked'
        user.email_verified_at = None if state == 'unverified' else utcnow()
        db.commit()
        response = client.post('/api/customer/auth/password-reset', json={'email': 'unknown@example.test' if state == 'unknown' else 'buyer@example.test'})
        assert response.status_code == 202 and set(response.json()) == {'flowId', 'resendAfterSeconds'}
        assert response.json()['resendAfterSeconds'] == 60
        assert 'token' not in response.text and 'buyer@example.test' not in response.text
        sent = deliver(db_engine)
        assert len(sent) == (1 if state == 'eligible' else 0)


def test_reset_resend_rejects_invalid_origin_equally_for_real_and_decoy_flows(client, mail_settings, partner_headers, monkeypatch):
    import app.accounts as accounts

    flows = [client.post('/api/customer/auth/password-reset', json={'email': email}).json()['flowId']
             for email in ('buyer@example.test', 'unknown@example.test')]
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(accounts, 'utcnow', lambda: later)
    monkeypatch.setattr(settings, 'public_site_url', 'https://shop.example/unsafe')
    for flow in flows:
        assert client.post('/api/customer/auth/resend', json={'flowId': flow}).status_code == 503


def test_worker_cancels_reset_mail_after_token_is_consumed(client, db_engine, db, mail_settings, partner_headers):
    from app.email_worker import process_once

    flow = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}).json()['flowId']
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    assert fresh_confirm(client, reset_body(flow, event.payload['code'])).status_code == 204
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Consumed reset link sent'))
    db.refresh(event)
    assert event.state == 'cancelled' and event.payload == {}


def test_worker_cancels_legacy_six_digit_reset_mail_even_with_matching_hmac(client, db_engine, db, mail_settings, partner_headers):
    from app.email_worker import process_once

    flow_id = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}).json()['flowId']
    flow = db.get(UserChallenge, flow_id)
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    event.payload = {**event.payload, 'code': '123456', 'subject': 'Код подтверждения MIRZAP67', 'text': 'Ваш код: 123456'}
    flow.code_hmac = code_digest(flow, '123456')
    db.commit()
    assert process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Unusable legacy reset code sent'))
    db.refresh(event)
    assert event.state == 'cancelled' and event.payload == {}


@pytest.mark.parametrize('length', [12, 256])
def test_reset_accepts_both_password_length_boundaries(client, db_engine, db, mail_settings, partner_headers, length):
    flow, token, _ = begin_reset(client, db_engine)
    password = 'P' * length
    response = fresh_confirm(client, reset_body(flow, token, password))
    assert response.status_code == 204, response.text
    db.expire_all()
    assert password_hasher.verify(db.get(User, 'partner').password_hash, password)


def test_reset_mail_limits_and_worker_expiry_remove_encrypted_token_without_logging_it(client, db_engine, db, mail_settings, partner_headers, monkeypatch, caplog):
    from app.email_worker import process_once

    for _ in range(5):
        assert client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}).status_code == 202
    assert client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'}).status_code == 429
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email').order_by(Outbox.created_at).limit(1))
    token = event.payload['code']
    monkeypatch.setattr(settings, 'smtp_host', '')
    now = utcnow() + timedelta(minutes=31)
    assert not process_once(session_factory=lambda: Session(db_engine), send=lambda _: pytest.fail('Expired reset sent'), now=now)
    db.expire_all()
    assert all(row.state == 'cancelled' and row.payload == {} for row in db.scalars(select(Outbox)))
    assert all(row.code_hmac is None and row.encrypted_payload is None for row in db.scalars(select(UserChallenge)))
    assert token not in caplog.text
