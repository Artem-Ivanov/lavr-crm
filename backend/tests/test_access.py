import pytest

from sqlalchemy import func, select


def test_login_roles_and_cookie_are_server_owned(client, admin_headers):
    response = client.get('/api/manager/session', headers=admin_headers)
    assert response.status_code == 200
    assert 'role' not in response.json()['user']
    assert 'isActive' not in response.json()['user']
    assert response.json()['capabilities'] == {'operate': True, 'editSite': True, 'manageUsers': True}
    assert client.get('/api/manager/session', headers={'Cookie': 'lavr_session=made-up'}).status_code == 401


def test_session_cookie_attributes_and_https_setting(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'cookie_secure', True)
    response = client.post('/api/manager/session', json={'login': 'admin@example.test', 'password': 'test-password-123'}, headers={'Origin': 'http://testserver'})
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/api'))


def test_login_requires_allowed_origin(client):
    assert client.post('/api/manager/session', json={'login': 'admin@example.test', 'password': 'test-password-123'}, headers={'Origin': 'https://evil.test'}).status_code == 403


def test_api_refuses_missing_session_secret(monkeypatch):
    import pytest
    from app.config import settings
    from app.main import create_app
    monkeypatch.setattr(settings, 'session_secret', '')
    with pytest.raises(RuntimeError, match='SESSION_SECRET'):
        create_app()


def test_private_endpoints_require_session_and_admin_role(client, manager_headers):
    assert client.get('/api/admin/site', headers={'Cookie': ''}).status_code == 401
    assert client.get('/api/admin/site', headers=manager_headers).status_code == 403


def test_logout_requires_origin_csrf_and_revokes_session(client, admin_headers):
    assert client.delete('/api/manager/session', headers={**admin_headers, 'X-CSRF-Token': 'bad'}).status_code == 403
    assert client.delete('/api/manager/session', headers={**admin_headers, 'Origin': 'https://evil.test'}).status_code == 403
    assert client.delete('/api/manager/session', headers=admin_headers).status_code == 204
    assert client.get('/api/manager/session', headers=admin_headers).status_code == 401


def test_passwords_and_session_tokens_are_not_stored_as_plain_text(client, db, admin_headers):
    from app.auth import User, UserSession
    staff = db.get(User, 'admin')
    assert staff.password_hash.startswith('$argon2')
    session = db.scalar(select(UserSession))
    assert session.token_hash != admin_headers['Cookie'].split('=', 1)[1]
    assert len(session.token_hash) == 64


def test_login_errors_are_generic_and_rate_limited(client):
    for _ in range(5):
        response = client.post('/api/manager/session', json={'login': 'unknown@example.test', 'password': 'wrong'}, headers={'Origin': 'http://testserver'})
        assert response.status_code == 401
        assert 'password' not in response.text
    response = client.post('/api/manager/session', json={'login': 'unknown@example.test', 'password': 'wrong'}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 429


def test_disabled_staff_sessions_are_rejected(client, db, admin_headers):
    from app.auth import User
    staff = db.get(User, 'admin')
    staff.is_active = False
    db.commit()
    assert client.get('/api/manager/session', headers=admin_headers).status_code == 401


def test_expired_staff_sessions_are_rejected(client, db, admin_headers):
    from datetime import timedelta
    from app.auth import UserSession
    from app.common import utcnow
    session = db.scalar(select(UserSession))
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get('/api/manager/session', headers=admin_headers).status_code == 401


def test_invalid_login_text_returns_safe_contract_error(client):
    response = client.post('/api/manager/session', json={'login': 'admin\x00', 'password': 'do-not-repeat-secret'}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 422
    assert isinstance(response.json()['details'], list)
    assert 'do-not-repeat-secret' not in response.text


def test_unknown_input_field_names_are_not_reflected(client):
    response = client.post('/api/manager/session', json={'login': 'admin@example.test', 'password': 'secret', 'private-value-as-key': 'unused'}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 422
    assert 'private-value-as-key' not in response.text
    assert response.json()['details'][0]['field'] == 'body'


def test_oversize_requests_reject_before_parsing(client):
    response = client.post('/api/admin/media', content=b'x' * (6 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_huge_content_length_rejects_safely(client):
    response = client.post('/api/admin/media', content=b'', headers={'Content-Length': '9' * 5000})
    assert response.status_code == 413


def test_unexpected_server_errors_do_not_expose_private_values(client):
    @client.app.get('/api/test-error')
    def fail():
        raise RuntimeError('do-not-repeat-private-value')

    response = client.get('/api/test-error')
    assert response.status_code == 500
    assert response.json()['code'] == 'INTERNAL_ERROR'
    assert 'do-not-repeat-private-value' not in response.text
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Request-ID'] == response.json()['requestId']


def test_partner_session_contains_only_customer_capabilities(client, partner_headers):
    response = client.get('/api/customer/session', headers=partner_headers)
    assert response.status_code == 200
    assert response.json()['capabilities'] == {'operate': False, 'editSite': False, 'manageUsers': False}
    assert response.json()['user'] == {'id': 'partner', 'name': 'Покупатель', 'email': 'buyer@example.test',
                                       'phone': '+79991234567', 'emailVerified': True, 'phoneVerified': False,
                                       'profileComplete': True}


def test_partner_cannot_access_operational_or_admin_apis(client, partner_headers):
    for path in ('/api/manager/orders', '/api/manager/feedback', '/api/manager/integration/health',
                 '/api/admin/users', '/api/admin/site', '/api/admin/categories'):
        assert client.get(path, headers=partner_headers).status_code == 403, path


def test_admin_inherits_all_operational_apis(client, admin_headers):
    for path in ('/api/manager/orders', '/api/manager/feedback', '/api/manager/integration/health'):
        assert client.get(path, headers=admin_headers).status_code == 200, path


def test_operator_capabilities_do_not_include_site_or_users(client, manager_headers):
    response = client.get('/api/customer/session', headers=manager_headers)
    assert response.json()['capabilities'] == {'operate': True, 'editSite': False, 'manageUsers': False}
    assert response.json()['user']['profileComplete'] is False
    assert response.json()['user']['email'] == 'manager@example.test'


def test_email_login_uses_verified_profile_same_identity_and_rotates_cookie(client, db, partner_headers):
    from app.auth import User, UserSession
    response = client.post('/api/customer/session', json={'email': ' BUYER@EXAMPLE.TEST ', 'password': 'test-password-123'},
                           headers=partner_headers)
    assert response.status_code == 200
    assert response.json()['user']['id'] == 'partner'
    assert response.cookies['lavr_session'] != partner_headers['Cookie'].split('=', 1)[1]
    assert client.get('/api/customer/session', headers=partner_headers).status_code == 401
    assert db.get(User, 'partner').role == 'partner'
    assert db.scalar(select(UserSession).where(UserSession.user_id == 'partner')) is not None


def test_email_login_errors_do_not_reveal_block_or_verification(client, db, partner_headers):
    from app.auth import User
    for state in ('unknown', 'blocked', 'unverified', 'wrong-password'):
        user = db.get(User, 'partner')
        user.is_active = state != 'blocked'
        from app.common import utcnow
        user.email_verified_at = None if state == 'unverified' else utcnow()
        db.commit()
        response = client.post('/api/customer/session', json={'email': 'missing@example.test' if state == 'unknown' else 'buyer@example.test',
                                                            'password': 'wrong' if state == 'wrong-password' else 'test-password-123'})
        assert response.status_code == 401
        assert response.json()['code'] == 'INVALID_CREDENTIALS'


def test_auth_version_change_revokes_customer_and_legacy_projections(client, db, partner_headers):
    from app.auth import User
    user = db.get(User, 'partner')
    user.auth_version += 1
    db.commit()
    for path in ('/api/customer/session', '/api/manager/session'):
        assert client.get(path, headers=partner_headers).status_code == 401


def test_guest_guard_rejects_any_presented_invalid_cookie_and_live_identity(client, partner_headers):
    from fastapi import Depends
    from app.auth import guest_only

    @client.app.post('/api/test-guest')
    def guest(allowed=Depends(guest_only)):
        return {'created': True}

    assert client.post('/api/test-guest', headers={'Cookie': ''}).status_code == 200
    for cookie in ('lavr_session=', 'lavr_session=invalid'):
        assert client.post('/api/test-guest', headers={'Cookie': cookie}).status_code == 401
    assert client.post('/api/test-guest', headers=partner_headers).status_code == 409
    assert client.post('/api/test-guest', headers={'Origin': 'https://evil.test'}).status_code == 403


def test_expired_cookie_can_be_cleared_only_with_allowed_origin(client, db, admin_headers):
    from datetime import timedelta
    from app.auth import UserSession
    from app.common import utcnow
    session = db.scalar(select(UserSession))
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.delete('/api/customer/session', headers={**admin_headers, 'Origin': 'https://evil.test'}).status_code == 403
    response = client.delete('/api/customer/session', headers={'Cookie': admin_headers['Cookie']})
    assert response.status_code == 204
    assert 'Max-Age=0' in response.headers['set-cookie']


@pytest.mark.parametrize('path,field', [('/api/manager/session', 'login'), ('/api/customer/session', 'email')])
def test_credentials_use_only_normalized_verified_email_without_recreating_identity(client, db, path, field):
    from app.auth import User, UserSession, hash_password
    from app.common import utcnow
    user = User(id='email-identity', login='old-username', name='Оператор', role='operator',
                password_hash=hash_password('test-password-123'), email_verified_at=utcnow())
    user.profile = {'email': 'identity@example.test'}
    db.add(user)
    db.commit()
    old_hash, old_login, old_version = user.password_hash, user.login_ciphertext, user.auth_version
    count = db.scalar(select(func.count()).select_from(User))
    response = client.post(path, json={field: ' IDENTITY@EXAMPLE.TEST ', 'password': 'test-password-123'}, headers={'Cookie': ''})
    assert response.status_code == 200, response.text
    assert response.json()['user']['id'] == 'email-identity'
    db.expire_all()
    user = db.get(User, 'email-identity')
    assert (user.password_hash, user.login_ciphertext, user.auth_version) == (old_hash, old_login, old_version)
    assert db.scalar(select(func.count()).select_from(User)) == count
    assert db.scalar(select(UserSession).where(UserSession.user_id == user.id)) is not None
    assert client.post(path, json={field: 'old-username', 'password': 'test-password-123'}).status_code == 422


@pytest.mark.parametrize('path,field', [('/api/manager/session', 'login'), ('/api/customer/session', 'email')])
@pytest.mark.parametrize('state', ['blocked', 'unverified', 'missing-email'])
def test_both_credentials_reject_ineligible_email_even_when_it_matches_legacy_login(client, db, path, field, state):
    from app.auth import User, hash_password
    from app.common import utcnow
    user = User(id='ineligible', login='ineligible@example.test', name='', role='operator',
                password_hash=hash_password('test-password-123'), is_active=state != 'blocked',
                email_verified_at=None if state == 'unverified' else utcnow())
    if state != 'missing-email':
        user.profile = {'email': 'ineligible@example.test'}
    db.add(user)
    db.commit()
    response = client.post(path, json={field: 'ineligible@example.test', 'password': 'test-password-123'})
    assert response.status_code == 401
    assert response.json()['code'] == 'INVALID_CREDENTIALS'


def test_credentials_share_throttle_across_routes_and_email_variants(client):
    for index in range(5):
        path, field, email = ('/api/manager/session', 'login', ' UNKNOWN@EXAMPLE.TEST ') if index % 2 else ('/api/customer/session', 'email', 'unknown@example.test')
        response = client.post(path, json={field: email, 'password': 'wrong'})
        assert response.status_code == 401
    for path, field in [('/api/manager/session', 'login'), ('/api/customer/session', 'email')]:
        response = client.post(path, json={field: 'unknown@example.test', 'password': 'wrong'})
        assert response.status_code == 429
        assert response.headers['Retry-After'] == '900'


def test_existing_emailless_session_survives_email_only_credential_change(client, db, admin_headers):
    from app.auth import User, UserSession
    user = db.get(User, 'admin')
    user.profile = {}
    user.email_verified_at = None
    db.commit()
    sessions = db.scalars(select(UserSession.id).where(UserSession.user_id == 'admin')).all()
    old_hash, old_version = user.password_hash, user.auth_version
    for path in ('/api/manager/session', '/api/customer/session'):
        assert client.get(path, headers=admin_headers).status_code == 200
    db.expire_all()
    assert db.get(User, 'admin').password_hash == old_hash
    assert db.get(User, 'admin').auth_version == old_version
    assert db.scalars(select(UserSession.id).where(UserSession.user_id == 'admin')).all() == sessions


def test_identity_pii_is_encrypted_and_profile_ciphertext_cannot_move(client, db, partner_headers):
    import json
    import pytest
    from app.auth import User
    values = db.execute(select(User.login_ciphertext, User.name_ciphertext, User.profile_ciphertext).where(User.id == 'partner')).one()
    saved = json.dumps(list(values), ensure_ascii=False)
    for value in ('buyer@example.test', '+79991234567', 'Покупатель', 'partner'):
        assert value not in saved
    user = db.get(User, 'partner')
    other = db.get(User, 'admin')
    other.profile_ciphertext = user.profile_ciphertext
    with pytest.raises(ValueError):
        _ = other.profile
    db.rollback()


def test_create_session_keeps_activation_transaction_atomic(client, db_engine):
    from fastapi import Request, Response
    from sqlalchemy.orm import Session
    from app.auth import User, UserSession, create_session, hash_password
    with Session(db_engine) as session:
        user = User(id='rollback-user', role='partner', password_hash=hash_password('test-password-123'))
        session.add(user)
        create_session(session, user, Request({'type': 'http', 'headers': []}), Response())
        session.rollback()
    with Session(db_engine) as session:
        assert session.get(User, 'rollback-user') is None
        assert session.scalar(select(UserSession).where(UserSession.user_id == 'rollback-user')) is None
