import base64
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

os.environ.setdefault('ALLOWED_ORIGINS', 'http://testserver')
os.environ.setdefault('SESSION_SECRET', 'test-session-secret-only-not-for-production-123456')
os.environ.setdefault('COOKIE_SECURE', 'false')
os.environ.setdefault('PII_ENCRYPTION_KEYS', json.dumps({'test': base64.urlsafe_b64encode(b'e' * 32).decode()}))
os.environ.setdefault('PII_ACTIVE_KEY_ID', 'test')
os.environ.setdefault('PII_INDEX_KEY', base64.urlsafe_b64encode(b'i' * 32).decode())
os.environ.setdefault('CHALLENGE_SECRET', 'test-challenge-secret-only-not-for-production-123456')


@pytest.fixture
def db_engine():
    from app.db import Base
    from app import auth, catalog, content, orders, feedback, accounts, catalog_imports  # Register every model before DDL.
    url = os.environ.get('TEST_DATABASE_URL')
    if not url or not make_url(url).database.endswith('_test'):
        pytest.fail('TEST_DATABASE_URL must reference a dedicated PostgreSQL *_test database')
    engine = create_engine(url, hide_parameters=True)
    schema = 'test_' + uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
    scoped = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(scoped)
    try:
        yield scoped
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        engine.dispose()


@pytest.fixture
def db(db_engine):
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def client(db_engine, db, tmp_path, monkeypatch):
    from app.auth import User, hash_password
    from app.config import settings
    from app.common import utcnow
    from app.db import get_db
    from app.main import create_app
    monkeypatch.setattr(settings, 'media_dir', tmp_path)
    monkeypatch.setattr(settings, 'allowed_origins', ('http://testserver',))
    for login, role in (('admin', 'admin'), ('manager', 'operator')):
        user = User(id=login, login=login, name=login, role=role,
                    password_hash=hash_password('test-password-123'), is_active=True, email_verified_at=utcnow())
        user.profile = {'email': f'{login}@example.test'}
        db.add(user)
    db.commit()
    app = create_app()

    def database():
        with Session(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = database
    with TestClient(app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as test_client:
        yield test_client


def login_headers(client, role):
    email = 'buyer@example.test' if role == 'partner' else f'{role}@example.test'
    response = client.post('/api/manager/session', json={'login': email, 'password': 'test-password-123'}, headers={'Origin': 'http://testserver', 'Cookie': ''})
    assert response.status_code == 200, response.text
    return {'Cookie': 'lavr_session=' + response.cookies['lavr_session'],
            'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrfToken']}


@pytest.fixture
def admin_headers(client):
    return login_headers(client, 'admin')


@pytest.fixture
def manager_headers(client):
    return login_headers(client, 'manager')


@pytest.fixture
def partner_headers(client, db):
    from app.auth import User, hash_password
    from app.common import utcnow
    user = User(id='partner', login='partner', name='Покупатель', role='partner',
                password_hash=hash_password('test-password-123'), email_verified_at=utcnow())
    user.profile = {'email': 'buyer@example.test', 'phone': '+79991234567'}
    db.add(user)
    db.commit()
    return login_headers(client, 'partner')
