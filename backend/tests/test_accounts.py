from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import ChallengeThrottle, UserChallenge
from app.auth import User, UserSession, password_hasher
from app.common import utcnow
from app.integration import Outbox


@pytest.fixture
def mail_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'customer_registration_enabled', True)
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'smtp_from', 'accounts@example.test')
    monkeypatch.setattr(settings, 'smtp_tls_mode', 'tls')


def signup(client, email='new@example.test', password=' chosen-password-123 ', phone='8 (999) 123-45-67', **kwargs):
    return client.post('/api/customer/auth/signup', json={'email': email, 'phone': phone, 'password': password, **kwargs})


def deliver(db_engine):
    from app.email_worker import process_once
    sent = []
    while process_once(session_factory=lambda: Session(db_engine), send=sent.append):
        pass
    return sent


def begin(client, db_engine, **kwargs):
    response = signup(client, **kwargs)
    assert response.status_code == 202, response.text
    sent = deliver(db_engine)
    assert sent
    return response.json()['flowId'], sent[-1]['code']


def continuation_headers(client, headers):
    flows = '; '.join(f'{key}={value}' for key, value in client.cookies.items() if key.startswith('lavr_flow_'))
    return {**headers, 'Cookie': headers['Cookie'] + '; ' + flows}


def activate(client, flow, code):
    return client.post('/api/customer/auth/activate', json={'flowId': flow, 'code': code})


def test_signup_delivers_code_and_atomically_activates_chosen_password(client, db_engine, db, mail_settings):
    response = signup(client, email=' NEW@Example.Test ')
    assert response.status_code == 202, response.text
    assert set(response.json()) == {'flowId', 'resendAfterSeconds'}
    assert response.json()['resendAfterSeconds'] == 60
    assert db.scalar(select(func.count()).select_from(User)) == 2
    assert sorted(db.scalars(select(ChallengeThrottle.count))) == [1, 1]
    sent = deliver(db_engine)
    assert len(sent) == 1
    assert sent[0]['to'] == 'new@example.test'
    assert len(sent[0]['code']) == 6 and sent[0]['code'].isdigit()
    activated = activate(client, response.json()['flowId'], sent[0]['code'])
    assert activated.status_code == 200, activated.text
    dto = activated.json()
    assert dto['user']['profileComplete'] and dto['user']['emailVerified']
    assert dto['user']['phone'] == '+79991234567'
    assert dto['capabilities'] == {'operate': False, 'editSite': False, 'manageUsers': False}
    assert 'role' not in dto['user'] and 'isActive' not in dto['user']
    assert 'lavr_session' in activated.cookies
    db.expire_all()
    user = db.get(User, dto['user']['id'])
    assert user.role == 'partner' and password_hasher.verify(user.password_hash, ' chosen-password-123 ')
    assert client.post('/api/customer/session', json={'email': 'new@example.test', 'password': ' chosen-password-123 '}).status_code == 200
    assert activate(client, response.json()['flowId'], sent[0]['code']).status_code == 400
    raw = str(db.execute(select(UserChallenge.encrypted_payload, UserChallenge.code_hmac)).all())
    assert 'new@example.test' not in raw and 'chosen-password' not in raw and sent[0]['code'] not in raw


@pytest.mark.parametrize('extra', [{'role': 'admin'}, {'capabilities': {}}, {'is_active': True}, {'customerId': 'admin'}])
def test_signup_rejects_server_fields(client, mail_settings, extra):
    assert signup(client, **extra).status_code == 422


@pytest.mark.parametrize('field,value', [('password', 'too-short'), ('password', 'x' * 257), ('password', 'valid-password\x00'), ('email', 'bad@example'), ('email', 'a\n@b.test'), ('phone', '+1234567890')])
def test_signup_validates_inputs(client, mail_settings, field, value):
    assert signup(client, **{field: value}).status_code == 422


def test_missing_smtp_and_signup_flag_are_safe(client, mail_settings, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'smtp_host', '')
    assert signup(client).status_code == 503
    assert client.post('/api/customer/auth/password-reset', json={'email': 'new@example.test'}).status_code == 503
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.test')
    monkeypatch.setattr(settings, 'customer_registration_enabled', False)
    assert signup(client).status_code == 503
    assert client.post('/api/customer/auth/password-reset', json={'email': 'new@example.test'}).status_code == 202


def test_continuation_is_per_flow_and_other_browser_cannot_consume(client, db_engine, mail_settings):
    from fastapi.testclient import TestClient
    one, first_code = begin(client, db_engine)
    two, second_code = begin(client, db_engine, password='second-password-123', phone='+78881234567')
    assert one != two
    with TestClient(client.app, headers={'Origin': 'http://testserver'}) as other:
        assert activate(other, one, first_code).status_code == 400
    assert activate(client, one, second_code).status_code == 400
    assert activate(client, one, first_code).status_code == 200
    assert activate(client, two, second_code).status_code == 400
    assert client.post('/api/customer/session', json={'email': 'new@example.test', 'password': 'second-password-123'}).status_code == 401


@pytest.mark.parametrize('blocked', [False, True])
def test_existing_email_never_replaced_even_when_blocked(client, db_engine, db, mail_settings, partner_headers, monkeypatch, blocked):
    import app.accounts as accounts
    hashes = []
    original_hash = accounts.hash_password
    def hash_password(password):
        hashes.append(password)
        return original_hash(password)
    monkeypatch.setattr(accounts, 'hash_password', hash_password)
    user = db.get(User, 'partner')
    before = user.profile, user.password_hash, user.role, user.auth_version
    user.is_active = not blocked
    db.commit()
    signup_response = signup(client, email='BUYER@EXAMPLE.TEST')
    assert hashes == [' chosen-password-123 ']
    assert sorted(db.scalars(select(ChallengeThrottle.count))) == [1, 1]
    recovery = client.post('/api/customer/auth/password-reset', json={'email': 'BUYER@EXAMPLE.TEST'})
    assert signup_response.status_code == recovery.status_code == 202
    assert set(signup_response.json()) == set(recovery.json()) == {'flowId', 'resendAfterSeconds'}
    assert activate(client, signup_response.json()['flowId'], '123456').status_code == 400
    sent = deliver(db_engine)
    assert len(sent) == (0 if blocked else 1)
    db.expire_all()
    user = db.get(User, 'partner')
    assert before == (user.profile, user.password_hash, user.role, user.auth_version)
    assert user.is_active is (not blocked)
    assert db.scalar(select(func.count()).select_from(User)) == 3


def test_resend_enforces_cooldown_rotates_generation_preserves_attempts(client, db_engine, db, mail_settings, monkeypatch):
    import app.accounts as accounts
    flow_id, old_code = begin(client, db_engine)
    assert activate(client, flow_id, '000000' if old_code != '000000' else '000001').status_code == 400
    response = client.post('/api/customer/auth/resend', json={'flowId': flow_id})
    assert response.status_code == 429 and int(response.headers['Retry-After']) > 0
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(accounts, 'utcnow', lambda: later)
    assert client.post('/api/customer/auth/resend', json={'flowId': flow_id}).status_code == 202
    sent = deliver(db_engine)
    assert len(sent) == 1
    db.expire_all()
    flow = db.get(accounts.UserChallenge, flow_id)
    assert flow.generation == 2 and flow.attempts == 1
    assert flow.created_at + timedelta(minutes=30) >= flow.expires_at
    assert activate(client, flow_id, old_code).status_code == 400
    assert activate(client, flow_id, sent[0]['code']).status_code == 200


def test_five_bad_codes_cannot_be_repaired_by_resend(client, db_engine, db, mail_settings, monkeypatch):
    import app.accounts as accounts
    flow_id, code = begin(client, db_engine)
    wrong = '000000' if code != '000000' else '000001'
    for _ in range(5):
        assert activate(client, flow_id, wrong).status_code == 400
    assert activate(client, flow_id, code).status_code == 429
    monkeypatch.setattr(accounts, 'utcnow', lambda: db.get(accounts.UserChallenge, flow_id).created_at + timedelta(seconds=61))
    assert client.post('/api/customer/auth/resend', json={'flowId': flow_id}).status_code == 429
    db.expire_all()
    assert db.get(accounts.UserChallenge, flow_id).attempts == 5


def test_code_expiry_and_flow_lifetime_survive_resend(client, db_engine, db, mail_settings, monkeypatch):
    import app.accounts as accounts
    flow_id, code = begin(client, db_engine)
    created = db.get(accounts.UserChallenge, flow_id).created_at
    monkeypatch.setattr(accounts, 'utcnow', lambda: created + timedelta(minutes=11))
    assert activate(client, flow_id, code).status_code == 400
    monkeypatch.setattr(accounts, 'utcnow', lambda: created + timedelta(minutes=29))
    assert client.post('/api/customer/auth/resend', json={'flowId': flow_id}).status_code == 202
    db.expire_all()
    assert db.get(accounts.UserChallenge, flow_id).expires_at == created + timedelta(minutes=30)
    monkeypatch.setattr(accounts, 'utcnow', lambda: created + timedelta(minutes=31))
    assert client.post('/api/customer/auth/resend', json={'flowId': flow_id}).status_code == 202
    assert activate(client, flow_id, code).status_code == 400


def test_email_mail_limit_is_durable_across_flows(client, db_engine, mail_settings):
    for _ in range(5):
        assert signup(client).status_code == 202
    response = signup(client)
    assert response.status_code == 429 and int(response.headers['Retry-After']) > 3500
    assert len(deliver(db_engine)) == 5


@pytest.mark.parametrize('scope,limit', [('ip', 20), ('email', 5)])
@pytest.mark.parametrize('email', ['new@example.test', 'buyer@example.test'])
def test_exhausted_signup_quota_rejects_before_password_hash(client, db, mail_settings, partner_headers, monkeypatch, scope, limit, email):
    import app.accounts as accounts
    from app.crypto import lookup_digest
    value = 'testclient' if scope == 'ip' else lookup_digest('email', email)
    key = lookup_digest('challenge_throttle', f'mail:{scope}:{value}')
    db.add(ChallengeThrottle(key=key, count=limit, window_start=utcnow()))
    db.commit()
    hashes = []
    original_hash = accounts.hash_password
    def hash_password(password):
        hashes.append(password)
        return original_hash(password)
    monkeypatch.setattr(accounts, 'hash_password', hash_password)
    response = signup(client, email=email)
    assert response.status_code == 429
    assert hashes == [], 'Rate-limited signup hashed a password'
    assert response.json()['code'] == 'CHALLENGE_RATE_LIMIT'
    assert int(response.headers['Retry-After']) > 3500
    db.expire_all()
    assert db.get(ChallengeThrottle, key).count == limit
    assert db.scalar(select(func.count()).select_from(UserChallenge)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0


def test_password_reset_revokes_all_target_sessions_and_preserves_other_users(client, db_engine, db, mail_settings, partner_headers, admin_headers):
    target_cookie = partner_headers['Cookie']
    response = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'})
    assert response.status_code == 202
    code = deliver(db_engine)[0]['code']
    flow = response.json()['flowId']
    wrong_purpose = activate(client, flow, code)
    assert wrong_purpose.status_code == 400
    before = db.get(User, 'partner').profile, db.get(User, 'partner').role
    response = client.post('/api/customer/auth/password-reset/confirm', json={'flowId': flow, 'code': code, 'newPassword': 'new-password-456'})
    assert response.status_code == 204, response.text
    assert client.get('/api/customer/session', headers={'Cookie': target_cookie}).status_code == 401
    assert client.get('/api/manager/session', headers=partner_headers).status_code == 401
    assert client.get('/api/customer/session', headers=admin_headers).status_code == 200
    db.expire_all()
    user = db.get(User, 'partner')
    assert (user.profile, user.role) == before
    assert user.auth_version == 2 and user.is_active
    assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user.id)) == 0
    assert client.post('/api/customer/session', json={'email': 'buyer@example.test', 'password': 'new-password-456'}).status_code == 200
    assert client.post('/api/customer/auth/password-reset/confirm', json={'flowId': flow, 'code': code, 'newPassword': 'another-password'}).status_code == 400


def test_profile_completion_updates_same_identity_without_changing_credentials(client, db_engine, db, mail_settings, manager_headers):
    user = db.get(User, 'manager')
    old_hash, old_id, old_login = user.password_hash, user.id, user.login
    response = client.post('/api/customer/profile/activation', json={'email': 'OPERATOR@EXAMPLE.TEST', 'phone': '8 (999) 123-45-67'}, headers=manager_headers)
    assert response.status_code == 202, response.text
    code = deliver(db_engine)[0]['code']
    response = client.post('/api/customer/profile/activation/confirm', json={'flowId': response.json()['flowId'], 'code': code}, headers=continuation_headers(client, manager_headers))
    assert response.status_code == 200, response.text
    assert response.json()['user']['id'] == old_id
    assert response.json()['capabilities']['operate']
    assert response.json()['user']['profileComplete']
    assert client.get('/api/customer/session', headers=manager_headers).status_code == 401
    db.expire_all()
    user = db.get(User, old_id)
    assert user.password_hash == old_hash and user.login == old_login and user.role == 'operator'
    assert user.profile == {'email': 'operator@example.test', 'phone': '+79991234567'}
    assert db.scalar(select(func.count()).select_from(User)) == 2


def test_profile_completion_requires_same_user_and_csrf(client, db_engine, mail_settings, manager_headers, admin_headers):
    body = {'email': 'operator@example.test', 'phone': '+79991234567'}
    assert client.post('/api/customer/profile/activation', json=body).status_code == 403
    response = client.post('/api/customer/profile/activation', json=body, headers=manager_headers)
    code = deliver(db_engine)[0]['code']
    confirm = {'flowId': response.json()['flowId'], 'code': code}
    assert client.post('/api/customer/profile/activation/confirm', json=confirm, headers=continuation_headers(client, admin_headers)).status_code == 400
    assert client.post('/api/customer/profile/activation/confirm', json=confirm, headers=continuation_headers(client, manager_headers)).status_code == 200


def test_parallel_activation_creates_one_identity_and_one_session(client, db_engine, db, mail_settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    flow, code = begin(client, db_engine)
    cookies = '; '.join(f'{key}={value}' for key, value in client.cookies.items())
    gate = Barrier(2)
    def confirm(_):
        with TestClient(client.app, headers={'Origin': 'http://testserver', 'Cookie': cookies}) as isolated:
            gate.wait()
            return activate(isolated, flow, code).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, range(2))) == [200, 400]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 3
    assert db.scalar(select(func.count()).select_from(UserSession)) == 1


def test_new_flow_does_not_bypass_hourly_email_check_limit(client, db_engine, mail_settings):
    flows = [begin(client, db_engine) for _ in range(5)]
    for flow, code in flows[:4]:
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(5):
            assert activate(client, flow, wrong).status_code == 400
    assert activate(client, *flows[4]).status_code == 429


def test_ip_limits_ignore_untrusted_forwarded_headers(client, db_engine, db, mail_settings):
    from app.accounts import ChallengeThrottle
    from app.crypto import lookup_digest
    now = utcnow()
    db.add(ChallengeThrottle(key=lookup_digest('challenge_throttle', 'mail:ip:testclient'), count=20, window_start=now))
    db.commit()
    response = client.post('/api/customer/auth/signup', json={'email': 'new@example.test', 'phone': '+79991234567', 'password': 'long-password-123'}, headers={'X-Real-IP': '8.8.8.8', 'X-Forwarded-For': '8.8.8.8'})
    assert response.status_code == 429
    flow = '00000000-0000-0000-0000-000000000000'
    db.add(ChallengeThrottle(key=lookup_digest('challenge_throttle', 'check:ip:testclient'), count=60, window_start=now))
    db.commit()
    assert activate(client, flow, '123456').status_code == 429


def test_parallel_independent_flows_for_same_email_cannot_overwrite_winner(client, db_engine, db, mail_settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    first = begin(client, db_engine, password='first-password-123', phone='+79991234567')
    second = begin(client, db_engine, password='second-password-123', phone='+78881234567')
    cookies = '; '.join(f'{key}={value}' for key, value in client.cookies.items())
    gate = Barrier(2)
    def confirm(pair):
        with TestClient(client.app, headers={'Origin': 'http://testserver', 'Cookie': cookies}) as isolated:
            gate.wait()
            response = activate(isolated, *pair)
            return pair[0], response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, [first, second]))
    assert sorted(status for _, status in results) == [200, 400]
    db.expire_all()
    from app.crypto import lookup_digest
    user = db.scalar(select(User).where(User.email_lookup == lookup_digest('email', 'new@example.test')))
    winner = next(flow for flow, status in results if status == 200)
    assert password_hasher.verify(user.password_hash, 'first-password-123' if winner == first[0] else 'second-password-123')
    assert user.profile['phone'] == ('+79991234567' if winner == first[0] else '+78881234567')
    assert db.scalar(select(func.count()).select_from(User)) == 3


def test_reset_started_before_admin_block_cannot_change_password(client, db_engine, db, mail_settings, partner_headers):
    response = client.post('/api/customer/auth/password-reset', json={'email': 'buyer@example.test'})
    code = deliver(db_engine)[0]['code']
    user = db.get(User, 'partner')
    old_hash = user.password_hash
    user.is_active, user.auth_version = False, user.auth_version + 1
    db.commit()
    response = client.post('/api/customer/auth/password-reset/confirm', json={'flowId': response.json()['flowId'], 'code': code, 'newPassword': 'new-password-456'})
    assert response.status_code == 400
    db.expire_all()
    assert db.get(User, 'partner').password_hash == old_hash
    assert not db.get(User, 'partner').is_active


def test_profile_completion_cannot_replace_another_blocked_email(client, db_engine, db, mail_settings, partner_headers, manager_headers):
    partner = db.get(User, 'partner')
    partner.is_active = False
    db.commit()
    response = client.post('/api/customer/profile/activation', json={'email': 'buyer@example.test', 'phone': '+78881234567'}, headers=manager_headers)
    assert response.status_code == 202
    assert not deliver(db_engine)
    assert client.post('/api/customer/profile/activation/confirm', json={'flowId': response.json()['flowId'], 'code': '123456'}, headers=continuation_headers(client, manager_headers)).status_code == 400
    db.expire_all()
    assert db.get(User, 'manager').profile == {'email': 'manager@example.test'}
    assert db.get(User, 'partner').profile['email'] == 'buyer@example.test'


def test_public_flow_mutations_require_allowed_origin(client, mail_settings):
    response = signup(client)
    flow = response.json()['flowId']
    for path, body in [('signup', {'email': 'other@example.test', 'phone': '+79991234567', 'password': 'long-password-123'}),
                       ('activate', {'flowId': flow, 'code': '123456'}), ('resend', {'flowId': flow}),
                       ('password-reset', {'email': 'new@example.test'}),
                       ('password-reset/confirm', {'flowId': flow, 'code': '123456', 'newPassword': 'long-password-123'})]:
        assert client.post('/api/customer/auth/' + path, json=body, headers={'Origin': 'https://evil.test'}).status_code == 403


def test_challenge_migration_matches_models_and_can_be_rolled_back(db_engine):
    from uuid import uuid4
    from sqlalchemy import inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.accounts import ChallengeThrottle
    from test_pii_migration import migration
    schema = 'challenge_migration_' + uuid4().hex
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    try:
        with db_engine.execution_options(schema_translate_map={None: schema}).begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
            with Operations.context(MigrationContext.configure(conn)):
                migration('0005').upgrade()
            for model in (UserChallenge, ChallengeThrottle):
                columns = inspect(conn).get_columns(model.__tablename__)
                assert {column['name'] for column in columns} == set(model.__table__.columns.keys())
                assert {column['name'] for column in columns if column['nullable']} == {column.name for column in model.__table__.columns if column.nullable}
            assert {index['name'] for index in inspect(conn).get_indexes('user_challenges')} == {'ix_user_challenges_email_lookup', 'ix_user_challenges_created_at'}
            with Operations.context(MigrationContext.configure(conn)):
                migration('0005').downgrade()
            assert not inspect(conn).get_table_names(schema=schema)
    finally:
        with db_engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')


def test_opaque_existing_email_flow_has_same_resend_cooldown_as_new_flow(client, db_engine, db, mail_settings, partner_headers, monkeypatch):
    import app.accounts as accounts
    existing = signup(client, email='buyer@example.test').json()['flowId']
    new = signup(client, email='new@example.test').json()['flowId']
    for flow in (existing, new):
        response = client.post('/api/customer/auth/resend', json={'flowId': flow})
        assert response.status_code == 429
        assert 0 < int(response.headers['Retry-After']) <= 60
    later = utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(accounts, 'utcnow', lambda: later)
    for flow in (existing, new):
        assert client.post('/api/customer/auth/resend', json={'flowId': flow}).status_code == 202
        assert client.post('/api/customer/auth/resend', json={'flowId': flow}).status_code == 429
    sent = deliver(db_engine)
    assert len(sent) == 1 and sent[0]['to'] == 'new@example.test'
    wrong = '000000' if sent[0]['code'] != '000000' else '000001'
    for flow in (existing, new):
        for _ in range(5):
            assert activate(client, flow, wrong).status_code == 400
        assert activate(client, flow, wrong).status_code == 429
    monkeypatch.setattr(accounts, 'utcnow', lambda: later + timedelta(seconds=61))
    for flow in (existing, new):
        assert client.post('/api/customer/auth/resend', json={'flowId': flow}).status_code == 429
