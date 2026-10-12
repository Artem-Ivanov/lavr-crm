import json
import os
import re
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .auth import Staff, hash_password
from .catalog import Category, CategoryCreate, Product, ProductPresentation
from .common import ApiError, new_id, utcnow
from .config import settings
from .content import Media, Publication, PublicationInput, SiteConfig, SitePatch, apply_publication, decode_image
from .db import SessionLocal
from .integration import IntegrationState


SEED_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_LOCK = 0x4C415652494E4954


def default_site():
    return {
        'hero': {'title': 'Автозапчасти для вашего автомобиля', 'description': 'Найдите нужную деталь по артикулу.',
                 'imageId': None, 'buttonText': 'Открыть каталог', 'target': {'type': 'catalog'}},
        'settings': {'storeName': 'LAVR', 'phone': '+79990000000', 'address': 'Адрес магазина не указан',
                     'hours': 'Уточняйте часы работы у менеджера', 'categoriesTitle': 'Категории запчастей',
                     'promotionsTitle': 'Акции и объявления', 'catalogTitle': 'Каталог автозапчастей'},
        'menu': [],
    }


def load_seed(db, created_files, now):
    seed = json.loads((SEED_ROOT / 'seed.json').read_text(encoding='utf-8'))
    if seed['source'] != 'demo':
        raise ValueError('Начальный каталог должен быть явно помечен как demo.')
    site = SitePatch.model_validate(seed['site']).model_dump(mode='json')
    for values in seed['categories']:
        category = CategoryCreate.model_validate({key: value for key, value in values.items() if key != 'id'})
        db.add(Category(id=values['id'], name=category.name, normalized_name=category.name.casefold(),
                        position=category.position, is_visible=category.isVisible, show_in_menu=category.showInMenu, version=1))
    db.flush()
    media_ids = {}
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    for asset in seed['media']:
        if not re.fullmatch(r'[a-z0-9-]+\.jpg', asset['file']):
            raise ValueError('Некорректное имя изображения начального каталога.')
        data = (SEED_ROOT / 'seed-media' / asset['file']).read_bytes()
        if not 0 < len(data) <= 5242880:
            raise ValueError('Некорректный размер изображения начального каталога.')
        width, height, extension = decode_image(data, 'image/jpeg')
        media_id = new_id()
        destination = settings.media_dir / f'{media_id}.{extension}'
        with destination.open('xb') as stream:
            created_files.append(destination)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        media_ids[asset['id']] = media_id
        db.add(Media(id=media_id, filename=destination.name, mime_type='image/jpeg', alt=asset['alt'],
                     width=width, height=height, size_bytes=len(data), state='ready', created_at=now))
    for values in seed['products']:
        product = Product(id=values['id'], external_id=values['externalId'], sku=values['sku'], brand=values['brand'],
                          name=values['name'], price_kopecks=values['priceKopecks'], stock=values['stock'],
                          reserved=0, is_active=True, delivery_days=values['deliveryDays'], updated_at=now)
        db.add(product)
        db.add(ProductPresentation(product_id=product.id, category_id=values['category'], website_title=None,
                                   website_description=values['websiteDescription'], image_ids=[media_ids[values['imageId']]], version=1))
    for values in seed['publications']:
        publication = Publication(id=values['id'], version=1)
        fields = PublicationInput.model_validate({key: value for key, value in values.items() if key != 'id'}).model_dump()
        fields['imageIds'] = [media_ids[media_id] for media_id in fields['imageIds']]
        apply_publication(publication, fields)
        db.add(publication)
    site['hero']['imageId'] = media_ids[site['hero']['imageId']]
    return site


def bootstrap(db):
    if not 16 <= len(settings.admin_password) <= 256 or '\x00' in settings.admin_password:
        raise ValueError('ADMIN_PASSWORD должен содержать от 16 до 256 символов.')
    if len(settings.session_secret) < 32:
        raise ValueError('SESSION_SECRET должен содержать не менее 32 символов.')
    login = settings.admin_login.strip()
    if not 1 <= len(login) <= 120 or re.search(r'[\x00-\x1f\x7f]', login):
        raise ValueError('Укажите корректный ADMIN_LOGIN.')
    created_files = []
    try:
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': BOOTSTRAP_LOCK})
        if db.scalar(select(Staff.id).where(Staff.role == 'admin').limit(1)) is None:
            db.add(Staff(id=new_id(), login=login, name='Администратор', role='admin',
                         password_hash=hash_password(settings.admin_password), is_active=True))
        site = db.get(SiteConfig, 1)
        state = db.get(IntegrationState, 1)
        if state is None:
            state = IntegrationState(id=1, state='not_connected')
            db.add(state)
        if site is None:
            fresh = all(db.scalar(select(model.id).limit(1)) is None for model in (Category, Product, Publication, Media))
            now = utcnow()
            values = load_seed(db, created_files, now) if settings.seed_demo_catalog and fresh else default_site()
            db.add(SiteConfig(id=1, version=1, **values))
            if settings.seed_demo_catalog and fresh and state.catalog_updated_at is None:
                state.catalog_updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        for path in created_files:
            path.unlink(missing_ok=True)
        raise


def main():
    try:
        with SessionLocal() as db:
            bootstrap(db)
    except (ValueError, KeyError, OSError, SQLAlchemyError, ApiError):
        raise SystemExit('Инициализация не выполнена. Проверьте секреты, PostgreSQL, каталог изображений и начальные данные.') from None
    print('Инициализация завершена.')


if __name__ == '__main__':
    main()
