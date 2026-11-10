from concurrent.futures import ThreadPoolExecutor
import re

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def bootstrap_settings(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, 'admin_login', ' OWNER@EXAMPLE.TEST ')
    monkeypatch.setattr(settings, 'admin_password', 'bootstrap-password-only-for-tests')
    monkeypatch.setattr(settings, 'session_secret', 'bootstrap-session-secret-only-for-tests-123')
    monkeypatch.setattr(settings, 'media_dir', tmp_path)
    monkeypatch.setattr(settings, 'seed_demo_catalog', False)
    return settings


@pytest.mark.parametrize('field,value', [('admin_password', 'a' * 15), ('session_secret', 'a' * 31)])
def test_bootstrap_rejects_short_secrets_before_creating_data(db, bootstrap_settings, monkeypatch, field, value):
    from app.bootstrap import bootstrap
    from app.auth import User
    from app.content import SiteConfig

    monkeypatch.setattr(bootstrap_settings, field, value)
    with pytest.raises(ValueError):
        bootstrap(db)
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.get(SiteConfig, 1) is None
    assert list(bootstrap_settings.media_dir.iterdir()) == []


def test_minimal_bootstrap_creates_real_admin_and_valid_site_without_demo_catalog(db, bootstrap_settings):
    from app.bootstrap import bootstrap
    from app.auth import User
    from app.catalog import Category, Product
    from app.content import Media, SiteConfig, SitePatch
    from app.integration import IntegrationState

    from app.crypto import lookup_digest

    bootstrap(db)
    admin = db.scalar(select(User).where(User.email_lookup == lookup_digest('email', 'owner@example.test')))
    assert admin.profile == {'email': 'owner@example.test'}
    assert admin.email_verified_at is not None
    assert admin.login is None
    assert admin.login_lookup is None
    assert admin.role == 'admin'
    assert admin.is_active is True
    assert admin.password_hash.startswith('$argon2id$')
    assert PasswordHasher().verify(admin.password_hash, bootstrap_settings.admin_password)
    site = db.get(SiteConfig, 1)
    SitePatch.model_validate({'hero': site.hero, 'settings': site.settings, 'menu': site.menu})
    assert site.version == 1
    assert site.hero['imageId'] is None
    assert db.get(IntegrationState, 1).state == 'not_connected'
    assert db.get(IntegrationState, 1).catalog_updated_at is None
    for model in (Category, Product, Media):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert list(bootstrap_settings.media_dir.iterdir()) == []


def test_bootstrap_restart_preserves_credentials_edits_and_disabled_seed(db, bootstrap_settings, monkeypatch):
    from app.bootstrap import bootstrap
    from app.auth import User
    from app.catalog import Product
    from app.content import SiteConfig
    from app.integration import IntegrationState

    bootstrap(db)
    admin = db.scalar(select(User).where(User.role == 'admin'))
    original_hash = admin.password_hash
    site = db.get(SiteConfig, 1)
    site.settings = {**site.settings, 'storeName': 'Настоящий магазин'}
    site.version = 8
    db.get(IntegrationState, 1).error_code = 'ERP_OFFLINE'
    db.commit()
    monkeypatch.setattr(bootstrap_settings, 'admin_password', 'different-password-not-a-reset')
    monkeypatch.setattr(bootstrap_settings, 'admin_login', 'new-environment-login')
    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)
    bootstrap(db)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.get(User, admin.id).password_hash == original_hash
    assert db.get(User, admin.id).login is None
    assert db.get(User, admin.id).login_lookup is None
    assert db.get(User, admin.id).profile == {'email': 'owner@example.test'}
    assert db.get(SiteConfig, 1).settings['storeName'] == 'Настоящий магазин'
    assert db.get(SiteConfig, 1).version == 8
    assert db.get(IntegrationState, 1).error_code == 'ERP_OFFLINE'
    assert db.scalar(select(func.count()).select_from(Product)) == 0
    assert list(bootstrap_settings.media_dir.iterdir()) == []


def test_seed_does_not_mix_demo_data_with_existing_imported_catalog(db, bootstrap_settings, monkeypatch):
    from app.bootstrap import bootstrap
    from app.catalog import Category, Product, ProductPresentation
    from app.common import utcnow
    from app.content import Media, SiteConfig

    category = Category(id='existing', name='Настоящий каталог', normalized_name='настоящий каталог')
    product = Product(id='imported', external_id='real-erp-id', sku='REAL-1', brand='Real', name='Импортированный товар', price_kopecks=12300, stock=3, updated_at=utcnow())
    db.add_all([category, product])
    db.flush()
    db.add(ProductPresentation(product_id=product.id, category_id=category.id, website_title='Редакторский текст'))
    db.commit()
    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)
    bootstrap(db)
    assert db.scalar(select(func.count()).select_from(Product)) == 1
    assert db.get(Product, 'imported').price_kopecks == 12300
    assert db.get(ProductPresentation, 'imported').website_title == 'Редакторский текст'
    assert db.get(SiteConfig, 1).hero['imageId'] is None
    assert db.scalar(select(func.count()).select_from(Media)) == 0
    assert list(bootstrap_settings.media_dir.iterdir()) == []


def test_explicit_seed_serves_database_catalog_and_persistent_image_bytes(client, db, bootstrap_settings, monkeypatch):
    from app.bootstrap import bootstrap
    from app.catalog import Category, Product, ProductPresentation
    from app.content import Media, Publication, SiteConfig
    from app.integration import DisabledAdapter, get_erp_adapter

    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)
    bootstrap(db)
    assert db.scalar(select(func.count()).select_from(Category)) == 6
    assert db.scalar(select(func.count()).select_from(Product)) == 14
    assert db.scalar(select(func.count()).select_from(ProductPresentation)) == 14
    assert db.scalar(select(func.count()).select_from(Publication)) == 3
    media = db.scalars(select(Media)).all()
    assert len(media) == 6
    for row in media:
        assert re.fullmatch(r'[a-f0-9-]{36}\.jpg', row.filename)
        assert (bootstrap_settings.media_dir / row.filename).stat().st_size == row.size_bytes
        response = client.get(f'/api/media/{row.id}')
        assert response.status_code == 200
        assert response.content == (bootstrap_settings.media_dir / row.filename).read_bytes()
    site_response = client.get('/api/site')
    assert site_response.status_code == 200
    site = site_response.json()
    assert site['hero']['title'] == 'Всё для вашего\nавтомобиля.\nВ одном месте.'
    assert len(site['publications']) == 3
    assert len(site['menu']) == 3
    search = client.get('/api/catalog', params={'q': 'W9142'})
    assert search.status_code == 200
    assert [row['id'] for row in search.json()['items']] == ['filter-mann-oil']
    product = search.json()['items'][0]
    assert product['priceKopecks'] == 89000
    assert product['image'].startswith('/api/media/')
    assert get_erp_adapter().is_configured is False
    assert isinstance(get_erp_adapter(), DisabledAdapter)
    initial_files = sorted(path.name for path in bootstrap_settings.media_dir.iterdir())
    changed_site = db.get(SiteConfig, 1)
    changed_site.hero = {**changed_site.hero, 'title': 'Изменённый заголовок'}
    changed_site.version = 2
    db.get(ProductPresentation, 'filter-mann-oil').website_title = 'Редакторский заголовок'
    db.commit()
    bootstrap(db)
    db.expire_all()
    assert db.get(SiteConfig, 1).hero['title'] == 'Изменённый заголовок'
    assert db.get(ProductPresentation, 'filter-mann-oil').website_title == 'Редакторский заголовок'
    assert sorted(path.name for path in bootstrap_settings.media_dir.iterdir()) == initial_files


def test_seed_database_failure_rolls_back_all_rows_and_only_new_files(db, bootstrap_settings, monkeypatch):
    from app.bootstrap import bootstrap
    from app.auth import User
    from app.catalog import Category, Product, ProductPresentation
    from app.content import Media, Publication, SiteConfig
    from app.integration import IntegrationState

    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)
    existing_file = bootstrap_settings.media_dir / 'keep-existing.txt'
    existing_file.write_text('Не удалять')

    def failed_commit():
        raise SQLAlchemyError('forced database failure')

    monkeypatch.setattr(db, 'commit', failed_commit)
    with pytest.raises(SQLAlchemyError):
        bootstrap(db)
    for model in (User, Category, Product, ProductPresentation, Media, Publication, SiteConfig, IntegrationState):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert list(bootstrap_settings.media_dir.iterdir()) == [existing_file]
    assert existing_file.read_text() == 'Не удалять'


def test_seed_disk_failure_rolls_back_database_and_partial_image_files(db, bootstrap_settings, monkeypatch):
    import app.bootstrap as bootstrap_module
    from app.auth import User
    from app.content import Media

    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)
    calls = 0

    def failed_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('forced media write error')

    monkeypatch.setattr(bootstrap_module.os, 'fsync', failed_fsync)
    with pytest.raises(OSError):
        bootstrap_module.bootstrap(db)
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(Media)) == 0
    assert list(bootstrap_settings.media_dir.iterdir()) == []


def test_parallel_bootstrap_initializes_one_dataset(db_engine, bootstrap_settings, monkeypatch):
    from app.bootstrap import bootstrap
    from app.auth import User
    from app.catalog import Product
    from app.content import Media

    monkeypatch.setattr(bootstrap_settings, 'seed_demo_catalog', True)

    def initialize(_):
        with Session(db_engine) as session:
            bootstrap(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(initialize, range(2)))
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Product)) == 14
        assert session.scalar(select(func.count()).select_from(Media)) == 6
    assert len(list(bootstrap_settings.media_dir.iterdir())) == 6


def test_manager_creation_uses_strong_password_and_never_overwrites_existing_staff(db):
    from app.manage_staff import create_manager
    from app.auth import User

    password = 'manager-password-only-for-tests'
    manager_id = create_manager(db, ' MANAGER@EXAMPLE.TEST ', 'Менеджер магазина', password)
    manager = db.get(User, manager_id)
    assert manager.profile == {'email': 'manager@example.test'}
    assert manager.email_verified_at is not None
    assert manager.role == 'operator'
    assert manager.is_active is True
    assert PasswordHasher().verify(manager.password_hash, password)
    original_hash = manager.password_hash
    with pytest.raises(ValueError):
        create_manager(db, ' MANAGER@EXAMPLE.TEST ', 'Другое имя', 'another-manager-password-for-tests')
    assert db.get(User, manager_id).password_hash == original_hash
    assert db.get(User, manager_id).name == 'Менеджер магазина'
    with pytest.raises(ValueError):
        create_manager(db, 'weak@example.test', 'Слабый пароль', 'a' * 15)
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_manager_cli_creates_staff_without_password_in_arguments_or_output(db, monkeypatch, capsys):
    import app.manage_staff as cli
    from app.auth import User
    from app.crypto import lookup_digest

    password = 'private-cli-password-only-for-tests'
    monkeypatch.setattr('sys.argv', ['app.manage_staff', ' CLI-MANAGER@EXAMPLE.TEST ', '--name', 'Менеджер CLI'])
    monkeypatch.setattr(cli, 'getpass', lambda prompt: password)
    monkeypatch.setattr(cli, 'SessionLocal', lambda: db)
    cli.main()
    saved = db.scalar(select(User).where(User.email_lookup == lookup_digest('email', 'cli-manager@example.test')))
    assert saved.role == 'operator'
    assert PasswordHasher().verify(saved.password_hash, password)
    output = capsys.readouterr()
    assert password not in output.out + output.err


def test_bootstrap_does_not_recreate_admin_after_permission_revocation(db, bootstrap_settings):
    from app.bootstrap import bootstrap
    from app.auth import User

    bootstrap(db)
    user = db.scalar(select(User))
    user.role = 'operator'
    user.is_active = False
    db.commit()
    bootstrap(db)
    db.expire_all()
    users = db.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].role == 'operator'
    assert users[0].is_active is False


@pytest.mark.parametrize('email', ['', 'username', 'bad@example', 'a\n@b.test'])
def test_new_bootstrap_requires_explicit_valid_email(db, bootstrap_settings, monkeypatch, email):
    from app.bootstrap import bootstrap
    from app.auth import User
    monkeypatch.setattr(bootstrap_settings, 'admin_login', email)
    with pytest.raises(ValueError):
        bootstrap(db)
    assert db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize('email', ['', 'username', 'bad@example', 'a\n@b.test'])
def test_manager_creation_rejects_username_and_invalid_email(db, email):
    from app.manage_staff import create_manager
    from app.auth import User
    with pytest.raises(ValueError):
        create_manager(db, email, 'Оператор', 'manager-password-only-for-tests')
    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_bootstrap_preserves_emailless_legacy_identity_and_sessions(db, bootstrap_settings, monkeypatch):
    from datetime import timedelta
    from app.auth import User, UserSession, hash_password
    from app.bootstrap import bootstrap
    from app.common import utcnow
    user = User(id='legacy', login='legacy-admin', name='Legacy', role='admin', password_hash=hash_password('old-password-123'))
    db.add(user)
    db.add(UserSession(id='legacy-session', user_id=user.id, token_hash='a' * 64, auth_version=1, expires_at=utcnow() + timedelta(days=1)))
    db.commit()
    original = (user.password_hash, user.login_ciphertext, user.auth_version)
    monkeypatch.setattr(bootstrap_settings, 'admin_login', 'legacy-admin')
    bootstrap(db)
    db.expire_all()
    user = db.get(User, 'legacy')
    assert (user.password_hash, user.login_ciphertext, user.auth_version) == original
    assert user.email_lookup is None and user.email_verified_at is None and user.profile == {}
    assert db.get(UserSession, 'legacy-session') is not None
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_manager_email_does_not_collide_with_a_different_users_historical_login(db):
    from app.auth import User, hash_password
    from app.manage_staff import create_manager

    legacy = User(id='legacy-email-shaped-login', login='operator@example.test', name='Existing', role='operator',
                  password_hash=hash_password('legacy-password-for-tests'))
    legacy.profile = {'email': 'different@example.test'}
    db.add(legacy)
    db.commit()
    original = (legacy.login_ciphertext, legacy.login_lookup, legacy.profile_ciphertext, legacy.password_hash, legacy.auth_version)
    manager_id = create_manager(db, ' OPERATOR@EXAMPLE.TEST ', 'New operator', 'manager-password-only-for-tests')
    manager = db.get(User, manager_id)
    assert manager.id != legacy.id
    assert manager.profile == {'email': 'operator@example.test'}
    assert manager.email_verified_at is not None
    assert manager.login_ciphertext is None and manager.login_lookup is None
    db.refresh(legacy)
    assert (legacy.login_ciphertext, legacy.login_lookup, legacy.profile_ciphertext, legacy.password_hash, legacy.auth_version) == original


def test_manager_creation_rejects_existing_canonical_email_without_changing_its_owner(db):
    from app.auth import User, hash_password
    from app.manage_staff import create_manager

    owner = User(id='canonical-email-owner', login='unrelated-old-login', name='Existing', role='partner',
                 password_hash=hash_password('legacy-password-for-tests'))
    owner.profile = {'email': 'operator@example.test'}
    db.add(owner)
    db.commit()
    original = (owner.login_ciphertext, owner.login_lookup, owner.profile_ciphertext, owner.password_hash, owner.auth_version, owner.role)
    with pytest.raises(ValueError, match='Email'):
        create_manager(db, ' OPERATOR@EXAMPLE.TEST ', 'New operator', 'manager-password-only-for-tests')
    db.refresh(owner)
    assert (owner.login_ciphertext, owner.login_lookup, owner.profile_ciphertext, owner.password_hash, owner.auth_version, owner.role) == original
    assert db.scalar(select(func.count()).select_from(User)) == 1
