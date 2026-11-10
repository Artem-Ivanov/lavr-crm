from sqlalchemy import select


def test_admin_lists_users_with_roles_and_versions(client, admin_headers):
    response = client.get('/api/admin/users', headers=admin_headers)
    assert response.status_code == 200
    assert response.json()['total'] == 2
    assert {item['role'] for item in response.json()['items']} == {'admin', 'operator'}
    for item in response.json()['items']:
        assert item['version'] == 1
        assert not {'password', 'password_hash', 'token_hash', 'profile_ciphertext'} & item.keys()


def test_operator_cannot_read_or_mutate_user_permissions(client, manager_headers):
    assert client.get('/api/admin/users', headers=manager_headers).status_code == 403
    assert client.patch('/api/admin/users/admin/permissions', json={'role': 'partner'},
                        headers={**manager_headers, 'If-Match': '"1"'}).status_code == 403


def test_last_active_admin_cannot_be_demoted_or_blocked(client, admin_headers):
    for endpoint, body in [('permissions', {'role': 'partner'}), ('status', {'isActive': False})]:
        response = client.patch('/api/admin/users/admin/' + endpoint, json=body,
                                headers={**admin_headers, 'If-Match': '"1"'})
        assert response.status_code == 409
        assert response.json()['code'] == 'LAST_ACTIVE_ADMIN'


def test_partner_cannot_read_or_mutate_users(client, partner_headers):
    assert client.get('/api/admin/users', headers=partner_headers).status_code == 403
    assert client.get('/api/admin/users/partner', headers=partner_headers).status_code == 403
    assert client.patch('/api/admin/users/partner/permissions', json={'role': 'admin'},
                        headers={**partner_headers, 'If-Match': '"1"'}).status_code == 403


def test_admin_user_detail_etag_validation_and_stale_update(client, admin_headers, db):
    from app.auth import AuditEntry
    response = client.get('/api/admin/users/manager', headers=admin_headers)
    assert response.status_code == 200
    assert response.headers['etag'] == '"1"'
    assert set(response.json()) == {'id', 'name', 'login', 'email', 'phone', 'emailVerified', 'phoneVerified', 'isActive', 'role', 'version'}
    invalid = [({'role': 'manager'}, '"1"', 422), ({'role': 'admin', 'isActive': True}, '"1"', 422),
               ({'role': 'admin'}, '', 400), ({'role': 'admin'}, '1', 400), ({'role': 'admin'}, '"2"', 412)]
    for body, version, status in invalid:
        result = client.patch('/api/admin/users/manager/permissions', json=body,
                              headers={**admin_headers, 'If-Match': version})
        assert result.status_code == status
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.permissions_changed')).all()


def test_promotion_preserves_identity_profile_hash_and_revokes_prior_sessions(client, db, admin_headers, partner_headers):
    from app.auth import User, AuditEntry
    old = db.get(User, 'partner')
    old_hash = old.password_hash
    old_profile = old.profile
    response = client.patch('/api/admin/users/partner/permissions', json={'role': 'operator'},
                            headers={**admin_headers, 'If-Match': '"1"'})
    assert response.status_code == 200
    assert response.json()['version'] == 2
    assert response.headers['etag'] == '"2"'
    assert client.get('/api/customer/session', headers=partner_headers).status_code == 401
    db.expire_all()
    user = db.get(User, 'partner')
    assert user.password_hash == old_hash
    assert user.profile == old_profile
    assert user.role == 'operator'
    audit = db.scalar(select(AuditEntry).where(AuditEntry.action == 'user.permissions_changed'))
    assert (audit.staff_id, audit.entity_id, audit.version) == ('admin', 'partner', 2)
    assert audit.details == {'before': {'role': 'partner', 'isActive': True}, 'after': {'role': 'operator', 'isActive': True}}
    assert 'buyer@example.test' not in str(audit.details)
    stale = client.patch('/api/admin/users/partner/status', json={'isActive': False}, headers={**admin_headers, 'If-Match': '"1"'})
    assert stale.status_code == 412


def test_block_and_unblock_invalidate_sessions_without_changing_role_or_profile(client, db, admin_headers, partner_headers):
    from app.auth import User
    response = client.patch('/api/admin/users/partner/status', json={'isActive': False}, headers={**admin_headers, 'If-Match': '"1"'})
    assert response.status_code == 200
    assert response.json()['isActive'] is False
    assert client.get('/api/customer/session', headers=partner_headers).status_code == 401
    assert client.post('/api/customer/session', json={'email': 'buyer@example.test', 'password': 'test-password-123'}).status_code == 401
    response = client.patch('/api/admin/users/partner/status', json={'isActive': True}, headers={**admin_headers, 'If-Match': '"2"'})
    assert response.status_code == 200
    assert response.json()['version'] == 3
    assert client.get('/api/customer/session', headers=partner_headers).status_code == 401
    db.expire_all()
    assert db.get(User, 'partner').role == 'partner'
    assert db.get(User, 'partner').profile['email'] == 'buyer@example.test'


def test_user_writes_require_csrf_origin_and_exact_fields(client, admin_headers):
    for headers in ({**admin_headers, 'X-CSRF-Token': ''}, {**admin_headers, 'Origin': 'https://evil.test'}):
        assert client.patch('/api/admin/users/manager/status', json={'isActive': False}, headers={**headers, 'If-Match': '"1"'}).status_code == 403
    for body in ({'isActive': 'false'}, {'isActive': False, 'password': 'ignored'}):
        assert client.patch('/api/admin/users/manager/status', json=body, headers={**admin_headers, 'If-Match': '"1"'}).status_code == 422
    assert client.get('/api/admin/users/missing', headers=admin_headers).status_code == 404


def test_users_search_matches_full_contacts_and_legacy_login_only(client, partner_headers, admin_headers):
    for q, expected in [(' BUYER@EXAMPLE.TEST ', ['partner']), ('8 (999) 123-45-67', ['partner']),
                        ('manager', ['manager']), ('buyer', []), ('999', []), ('MANAGER', [])]:
        response = client.get('/api/admin/users', params={'q': q}, headers=admin_headers)
        assert response.status_code == 200
        assert [item['id'] for item in response.json()['items']] == expected
    response = client.get('/api/admin/users', params={'offset': 1, 'limit': 1}, headers=admin_headers)
    assert response.json()['total'] == 3
    assert response.json()['offset'] == 1
    assert response.json()['limit'] == 1
    assert len(response.json()['items']) == 1


def test_parallel_admin_self_demotion_and_block_preserve_one_active_admin(client, db, db_engine, admin_headers, manager_headers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from sqlalchemy import func
    from sqlalchemy.orm import Session
    from app.auth import User
    from conftest import login_headers

    manager = db.get(User, 'manager')
    manager.role = 'admin'
    manager.auth_version += 1
    db.commit()
    other_headers = login_headers(client, 'manager')
    gate = Barrier(2)

    def change(args):
        target, endpoint, body, headers, version = args
        with TestClient(client.app, headers={'Origin': 'http://testserver', 'Cookie': ''}) as isolated:
            gate.wait()
            return isolated.patch('/api/admin/users/' + target + '/' + endpoint, json=body,
                                  headers={**headers, 'If-Match': version}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [('admin', 'permissions', {'role': 'partner'}, admin_headers, '"1"'),
                                         ('manager', 'status', {'isActive': False}, other_headers, '"2"')]))
    assert sorted(results) == [200, 409]
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.role == 'admin', User.is_active.is_(True))) == 1


def test_self_role_change_revokes_admin_session_immediately(client, db, admin_headers):
    from app.auth import User
    manager = db.get(User, 'manager')
    manager.role = 'admin'
    db.commit()
    response = client.patch('/api/admin/users/admin/permissions', json={'role': 'partner'}, headers={**admin_headers, 'If-Match': '"1"'})
    assert response.status_code == 200
    assert client.get('/api/admin/users', headers=admin_headers).status_code == 401
    assert client.get('/api/manager/orders', headers=admin_headers).status_code == 401


def test_user_login_projection_is_email_without_exposing_historical_username(client, db, admin_headers):
    from app.auth import User
    manager = db.get(User, 'manager')
    manager.login = 'historical-private-username'
    manager.profile = {'email': 'operator@example.test'}
    db.commit()
    response = client.get('/api/admin/users/manager', headers=admin_headers)
    assert response.status_code == 200
    assert response.json()['login'] == response.json()['email'] == 'operator@example.test'
    manager.profile = {}
    db.commit()
    response = client.get('/api/admin/users/manager', headers=admin_headers)
    assert response.json()['login'] is None
    assert 'historical-private-username' not in response.text
