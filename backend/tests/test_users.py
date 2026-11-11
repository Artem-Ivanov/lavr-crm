import pytest
from sqlalchemy import select


CREATE_USER = {'email': 'new-user@example.test', 'password': 'new-user-password-123'}


@pytest.mark.parametrize('role', ['partner', 'operator', 'admin'])
def test_admin_creates_immediately_usable_user_without_exposing_secrets(client, db, admin_headers, role):
    from app.auth import AuditEntry, User, password_hasher
    from app.crypto import lookup_digest
    from app.integration import Outbox

    body = {**CREATE_USER, 'email': ' NEW-USER@EXAMPLE.TEST ', 'name': '  Новый пользователь  ',
            'phone': '8 (999) 123-45-67', 'role': role}
    response = client.post('/api/admin/users', json=body, headers=admin_headers)
    assert response.status_code == 201
    created = response.json()
    assert created == {'id': created['id'], 'name': 'Новый пользователь', 'login': CREATE_USER['email'],
                       'email': CREATE_USER['email'], 'phone': '+79991234567', 'emailVerified': True,
                       'phoneVerified': False, 'isActive': True, 'role': role, 'version': 1}
    assert response.headers['etag'] == '"1"'
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert client.get('/api/customer/session', headers=admin_headers).json()['user']['id'] == 'admin'
    saved = db.get(User, created['id'])
    assert saved.login is None and saved.login_lookup is None
    assert saved.email_lookup == lookup_digest('email', CREATE_USER['email'])
    assert password_hasher.verify(saved.password_hash, CREATE_USER['password'])
    assert saved.password_hash != CREATE_USER['password'] and saved.email_verified_at is not None
    assert CREATE_USER['email'] not in str(saved.profile_ciphertext)
    assert '+79991234567' not in str(saved.profile_ciphertext)
    assert body['name'].strip() not in saved.name_ciphertext
    assert CREATE_USER['password'] not in response.text and saved.password_hash not in response.text
    audit = db.scalar(select(AuditEntry).where(AuditEntry.action == 'user.created'))
    assert (audit.staff_id, audit.entity_id, audit.version) == ('admin', created['id'], 1)
    assert audit.details == {'role': role, 'isActive': True}
    assert not db.scalars(select(Outbox)).all()
    login = client.post('/api/customer/session', json=CREATE_USER, headers={'Cookie': '', 'Origin': 'http://testserver'})
    assert login.status_code == 200
    assert login.json()['user']['id'] == created['id']
    assert login.json()['capabilities'] == {'operate': role != 'partner', 'editSite': role == 'admin', 'manageUsers': role == 'admin'}


def test_admin_creation_defaults_optional_profile_fields_and_partner_role(client, admin_headers):
    response = client.post('/api/admin/users', json=CREATE_USER, headers=admin_headers)
    assert response.status_code == 201
    assert response.json()['name'] == '' and response.json()['phone'] is None
    assert response.json()['role'] == 'partner'


def test_admin_creation_accepts_explicit_empty_profile_fields(client, admin_headers):
    response = client.post('/api/admin/users', json={**CREATE_USER, 'name': '  ', 'phone': ''}, headers=admin_headers)
    assert response.status_code == 201
    assert response.json()['name'] == '' and response.json()['phone'] is None


def test_admin_creation_requires_current_admin_origin_and_csrf(client, admin_headers, manager_headers, partner_headers, db):
    from app.auth import AuditEntry, User
    headers_and_status = [({'Cookie': '', 'Origin': 'http://testserver'}, 401), (manager_headers, 403),
                          (partner_headers, 403), ({**admin_headers, 'Origin': 'https://evil.test'}, 403),
                          ({**admin_headers, 'X-CSRF-Token': ''}, 403)]
    for headers, status in headers_and_status:
        assert client.post('/api/admin/users', json=CREATE_USER, headers=headers).status_code == status
    assert len(db.scalars(select(User)).all()) == 3
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()


def test_admin_creation_rejects_invalid_or_untrusted_fields_without_side_effects(client, admin_headers, db):
    from app.auth import AuditEntry, User
    for patch in [{'email': 'not-an-email'}, {'email': None}, {'password': 'short'}, {'password': 'x' * 257},
                  {'password': 'long-password\x00'}, {'role': 'manager'}, {'role': None}, {'phone': '123'},
                  {'phone': None}, {'name': 'x' * 241}, {'name': 'bad\nname'}, {'name': None},
                  {'isActive': False}, {'emailVerified': True}, {'auth_version': 2}, {'password_hash': 'raw'},
                  {'capabilities': {'manageUsers': True}}]:
        response = client.post('/api/admin/users', json={**CREATE_USER, **patch}, headers=admin_headers)
        assert response.status_code == 422, patch.keys()
        assert CREATE_USER['password'] not in response.text
    assert len(db.scalars(select(User)).all()) == 2
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()


def test_admin_creation_duplicate_email_never_changes_existing_identity(client, admin_headers, db):
    from app.auth import AuditEntry, User
    before = db.get(User, 'manager')
    original = (before.role, before.password_hash, before.profile, before.auth_version)
    response = client.post('/api/admin/users', json={**CREATE_USER, 'email': ' MANAGER@EXAMPLE.TEST ', 'role': 'admin'}, headers=admin_headers)
    assert response.status_code == 409 and response.json()['code'] == 'EMAIL_ALREADY_EXISTS'
    db.expire_all()
    saved = db.get(User, 'manager')
    assert (saved.role, saved.password_hash, saved.profile, saved.auth_version) == original
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()


def test_admin_creation_rolls_back_identity_when_audit_cannot_be_saved(client, db_engine, db, admin_headers):
    from sqlalchemy import text
    from app.auth import User

    schema = db_engine.get_execution_options()['schema_translate_map'][None]
    db.execute(text(f'ALTER TABLE "{schema}".audit_entries ADD CONSTRAINT reject_creation_audit CHECK (action <> \'user.created\')'))
    db.commit()
    response = client.post('/api/admin/users', json=CREATE_USER, headers=admin_headers)
    assert response.status_code == 409
    db.expire_all()
    assert len(db.scalars(select(User)).all()) == 2


def test_signup_started_before_admin_creation_cannot_overwrite_created_account(client, db, admin_headers, monkeypatch):
    from app.auth import User, password_hasher
    from app.config import settings
    from app.integration import Outbox

    for key, value in {'customer_registration_enabled': True, 'smtp_host': 'smtp.example.test',
                       'smtp_from': 'accounts@example.test', 'smtp_tls_mode': 'tls'}.items():
        monkeypatch.setattr(settings, key, value)
    signup = client.post('/api/customer/auth/signup', json={**CREATE_USER, 'password': 'signup-password-123', 'phone': '+79991234567'},
                         headers={'Cookie': '', 'Origin': 'http://testserver'})
    assert signup.status_code == 202
    flow_id = signup.json()['flowId']
    event = db.scalar(select(Outbox).where(Outbox.event_type == 'customer_verification_email'))
    code = event.payload['code']
    response = client.post('/api/admin/users', json={**CREATE_USER, 'role': 'admin'}, headers=admin_headers)
    assert response.status_code == 201
    flow_cookie = '; '.join(f'{key}={value}' for key, value in client.cookies.items() if key.startswith('lavr_flow_'))
    activation = client.post('/api/customer/auth/activate', json={'flowId': flow_id, 'code': code},
                             headers={'Cookie': flow_cookie, 'Origin': 'http://testserver'})
    assert activation.status_code == 400 and activation.json()['code'] == 'INVALID_CHALLENGE'
    db.expire_all()
    user = db.get(User, response.json()['id'])
    assert user.role == 'admin' and password_hasher.verify(user.password_hash, CREATE_USER['password'])


def test_parallel_admin_creation_creates_one_identity_and_audit(client, db_engine, admin_headers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from app.auth import AuditEntry, User
    from app.crypto import lookup_digest

    gate = Barrier(2)
    def create(index):
        with TestClient(client.app, headers={'Cookie': '', 'Origin': 'http://testserver'}) as isolated:
            gate.wait()
            return isolated.post('/api/admin/users', json={**CREATE_USER, 'role': 'admin'}, headers=admin_headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert next(response for response in responses if response.status_code == 409).json()['code'] == 'EMAIL_ALREADY_EXISTS'
    with Session(db_engine) as session:
        assert len(session.scalars(select(User).where(User.email_lookup == lookup_digest('email', CREATE_USER['email']))).all()) == 1
        assert len(session.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()) == 1


def test_admin_creation_handles_duplicate_from_cli_between_check_and_commit(client, db_engine, db, admin_headers):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.auth import AuditEntry, User, password_hasher
    from app.crypto import lookup_digest
    from app.manage_staff import create_manager

    digest = lookup_digest('email', CREATE_USER['email'])
    concurrent = []
    def insert_competing_identity(conn, cursor, statement, parameters, context, executemany):
        if not concurrent and statement.startswith('INSERT INTO ') and '.users ' in statement and parameters.get('email_lookup') == digest:
            concurrent.append(True)
            with Session(db_engine) as competing:
                concurrent.append(create_manager(competing, CREATE_USER['email'], 'Менеджер из CLI', 'cli-only-password-123'))
    event.listen(db_engine, 'before_cursor_execute', insert_competing_identity)
    try:
        response = client.post('/api/admin/users', json={**CREATE_USER, 'role': 'admin'}, headers=admin_headers)
    finally:
        event.remove(db_engine, 'before_cursor_execute', insert_competing_identity)
    assert len(concurrent) == 2
    assert response.status_code == 409 and response.json()['code'] == 'EMAIL_ALREADY_EXISTS'
    db.expire_all()
    saved = db.get(User, concurrent[1])
    assert saved.role == 'operator' and saved.name == 'Менеджер из CLI'
    assert password_hasher.verify(saved.password_hash, 'cli-only-password-123')
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()


@pytest.mark.parametrize('revoked', ['role', 'is_active', 'auth_version'])
def test_admin_creation_rechecks_identity_after_waiting_for_permission_lock(client, db_engine, db, admin_headers, revoked):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi.testclient import TestClient
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session
    from app.auth import AuditEntry, User
    from app.users import USER_PERMISSIONS_LOCK

    waiting = Event()
    def before_execute(conn, cursor, statement, parameters, context, executemany):
        if 'pg_advisory_xact_lock' in statement:
            waiting.set()
    def create():
        with TestClient(client.app, headers={'Cookie': '', 'Origin': 'http://testserver'}) as isolated:
            return isolated.post('/api/admin/users', json={**CREATE_USER, 'role': 'admin'}, headers=admin_headers)
    with Session(db_engine) as blocker:
        blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': USER_PERMISSIONS_LOCK})
        event.listen(db_engine, 'before_cursor_execute', before_execute)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(create)
                try:
                    assert waiting.wait(10), 'Create did not wait for the permission lock'
                    admin = db.get(User, 'admin')
                    setattr(admin, revoked, {'role': 'partner', 'is_active': False, 'auth_version': 2}[revoked])
                    db.commit()
                finally:
                    blocker.commit()
                response = pending.result(timeout=10)
        finally:
            event.remove(db_engine, 'before_cursor_execute', before_execute)
    assert response.status_code == 401
    db.expire_all()
    assert len(db.scalars(select(User)).all()) == 2
    assert not db.scalars(select(AuditEntry).where(AuditEntry.action == 'user.created')).all()


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
