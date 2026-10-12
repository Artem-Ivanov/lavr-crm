from sqlalchemy import select


def test_login_roles_and_cookie_are_server_owned(client, admin_headers):
    response = client.get('/api/manager/session', headers=admin_headers)
    assert response.status_code == 200
    assert response.json()['user']['role'] == 'admin'
    assert client.get('/api/manager/session', headers={'Cookie': 'lavr_session=made-up'}).status_code == 401


def test_session_cookie_attributes_and_https_setting(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'cookie_secure', True)
    response = client.post('/api/manager/session', json={'login': 'admin', 'password': 'test-password-123'}, headers={'Origin': 'http://testserver'})
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/api'))


def test_login_requires_allowed_origin(client):
    assert client.post('/api/manager/session', json={'login': 'admin', 'password': 'test-password-123'}, headers={'Origin': 'https://evil.test'}).status_code == 403


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
    from app.auth import Staff, StaffSession
    staff = db.get(Staff, 'admin')
    assert staff.password_hash.startswith('$argon2')
    session = db.scalar(select(StaffSession))
    assert session.token_hash != admin_headers['Cookie'].split('=', 1)[1]
    assert len(session.token_hash) == 64


def test_login_errors_are_generic_and_rate_limited(client):
    for _ in range(5):
        response = client.post('/api/manager/session', json={'login': 'unknown', 'password': 'wrong'}, headers={'Origin': 'http://testserver'})
        assert response.status_code == 401
        assert 'password' not in response.text
    response = client.post('/api/manager/session', json={'login': 'unknown', 'password': 'wrong'}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 429


def test_disabled_staff_sessions_are_rejected(client, db, admin_headers):
    from app.auth import Staff
    staff = db.get(Staff, 'admin')
    staff.is_active = False
    db.commit()
    assert client.get('/api/manager/session', headers=admin_headers).status_code == 401


def test_expired_staff_sessions_are_rejected(client, db, admin_headers):
    from datetime import timedelta
    from app.auth import StaffSession
    from app.common import utcnow
    session = db.scalar(select(StaffSession))
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get('/api/manager/session', headers=admin_headers).status_code == 401


def test_invalid_login_text_returns_safe_contract_error(client):
    response = client.post('/api/manager/session', json={'login': 'admin\x00', 'password': 'do-not-repeat-secret'}, headers={'Origin': 'http://testserver'})
    assert response.status_code == 422
    assert isinstance(response.json()['details'], list)
    assert 'do-not-repeat-secret' not in response.text


def test_unknown_input_field_names_are_not_reflected(client):
    response = client.post('/api/manager/session', json={'login': 'admin', 'password': 'secret', 'private-value-as-key': 'unused'}, headers={'Origin': 'http://testserver'})
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
