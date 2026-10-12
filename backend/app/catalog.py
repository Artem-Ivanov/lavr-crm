import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, case, event, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import record_audit, require_admin, require_admin_mutation
from .common import ApiError, check_version, new_id, page_response, utcnow
from .db import Base, get_db


class Category(Base):
    __tablename__ = 'categories'
    __table_args__ = (
        CheckConstraint('position BETWEEN 0 AND 1000000'),
        CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    normalized_name: Mapped[str] = mapped_column(String(240), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_menu: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class Product(Base):
    __tablename__ = 'products'
    __table_args__ = (
        CheckConstraint('price_kopecks BETWEEN 0 AND 9007199254740991'),
        CheckConstraint('stock BETWEEN 0 AND 2147483647'),
        CheckConstraint('reserved BETWEEN 0 AND 2147483647'),
        CheckConstraint('delivery_days BETWEEN 0 AND 365'),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    sku: Mapped[str] = mapped_column(String(80))
    brand: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(240))
    price_kopecks: Mapped[int] = mapped_column(BigInteger)
    stock: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_days: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    search_normalized: Mapped[str] = mapped_column(Text, index=True, default='')


class ProductPresentation(Base):
    __tablename__ = 'product_presentations'
    __table_args__ = (CheckConstraint('version BETWEEN 1 AND 9007199254740991'),)

    product_id: Mapped[str] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), primary_key=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), index=True)
    website_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    website_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_ids: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(BigInteger, default=1)


def normalize_search(value):
    return re.sub(r'[\W_]+', '', value.casefold())


@event.listens_for(Product, 'before_insert')
@event.listens_for(Product, 'before_update')
def update_search_index(mapper, connection, product):
    product.search_normalized = ' '.join(normalize_search(value) for value in [product.sku, product.name, product.brand])


Id = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^[^\x00-\x1f\x7f]+$')]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    name: str = Field(min_length=1, max_length=80)
    position: int = Field(ge=0, le=1000000)
    isVisible: bool
    showInMenu: bool

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value):
        if '\x00' in value:
            raise ValueError('Название содержит недопустимый символ.')
        value = ' '.join(value.split())
        if not value:
            raise ValueError('Введите название категории.')
        return value


class CategoryPatch(CategoryCreate):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    position: int | None = Field(default=None, ge=0, le=1000000)
    isVisible: bool | None = None
    showInMenu: bool | None = None

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value):
        return CategoryCreate.normalize_name(value) if value is not None else value

    @model_validator(mode='after')
    def check_patch(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Передайте хотя бы одно корректное поле категории.')
        return self


class PresentationPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    categoryId: Id | None = None
    websiteTitle: str | None = Field(default=None, min_length=1, max_length=240)
    websiteDescription: str | None = Field(default=None, max_length=4000)
    imageIds: list[Id] | None = Field(default=None, max_length=8)

    @field_validator('websiteTitle')
    @classmethod
    def require_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Заголовок не может быть пустым.')
        return value

    @field_validator('websiteTitle', 'websiteDescription')
    @classmethod
    def reject_nul(cls, value):
        if value is not None and '\x00' in value:
            raise ValueError('Текст содержит недопустимый символ.')
        return value

    @model_validator(mode='after')
    def check_patch(self):
        if not self.model_fields_set:
            raise ValueError('Передайте хотя бы одно поле оформления.')
        if 'categoryId' in self.model_fields_set and self.categoryId is None:
            raise ValueError('Выберите категорию.')
        if 'imageIds' in self.model_fields_set and (self.imageIds is None or len(set(self.imageIds)) != len(self.imageIds)):
            raise ValueError('Изображения должны иметь разные идентификаторы.')
        return self


def public_category(category):
    return {'id': category.id, 'name': category.name, 'position': category.position, 'showInMenu': category.show_in_menu}


def admin_category(category):
    return {**public_category(category), 'isVisible': category.is_visible, 'version': category.version}


def presentation_response(presentation):
    return {'productId': presentation.product_id, 'categoryId': presentation.category_id, 'websiteTitle': presentation.website_title,
            'websiteDescription': presentation.website_description, 'imageIds': presentation.image_ids, 'version': presentation.version}


def public_product(db, product, presentation, category):
    from .content import Media, public_media

    images = []
    if product.is_active and category.is_visible and presentation.image_ids:
        media = {row.id: row for row in db.scalars(select(Media).where(Media.id.in_(presentation.image_ids), Media.state == 'ready'))}
        images = [public_media(media[media_id]) for media_id in presentation.image_ids if media_id in media]
    return {'id': product.id, 'externalId': product.external_id, 'sku': product.sku, 'brand': product.brand, 'name': product.name,
            'category': presentation.category_id, 'priceKopecks': product.price_kopecks, 'stock': max(0, product.stock - product.reserved),
            'image': images[0]['path'] if images else '', 'images': images, 'websiteTitle': presentation.website_title,
            'websiteDescription': presentation.website_description, 'deliveryDays': product.delivery_days}


def admin_product(product, presentation, category):
    return {'id': product.id, 'sku': product.sku, 'brand': product.brand, 'name': product.name, 'category': presentation.category_id,
            'priceKopecks': product.price_kopecks, 'stock': max(0, product.stock - product.reserved), 'isVisible': product.is_active and category.is_visible}


def catalog_metadata(db):
    from .integration import IntegrationState

    state = db.get(IntegrationState, 1)
    updated_at = state.catalog_updated_at if state and state.catalog_updated_at else db.scalar(select(func.max(Product.updated_at)))
    stale = updated_at is None or utcnow() - updated_at > timedelta(days=1)
    return {'catalogUpdatedAt': (updated_at or datetime(1970, 1, 1, tzinfo=timezone.utc)).isoformat(), 'stale': stale}


def catalog_query():
    return select(Product, ProductPresentation, Category).join(ProductPresentation, ProductPresentation.product_id == Product.id).join(Category, Category.id == ProductPresentation.category_id)


def is_target_available(db, target):
    if not isinstance(target, dict):
        return False
    if target.get('type') == 'catalog':
        return set(target) == {'type'}
    if target.get('type') == 'section':
        return set(target) == {'type', 'section'} and target.get('section') in {'offers', 'announcements', 'hot'}
    if target.get('type') == 'category' and set(target) == {'type', 'categoryId'} and isinstance(target.get('categoryId'), str):
        return db.scalar(select(Category.id).where(Category.id == target['categoryId'], Category.is_visible)) is not None
    if target.get('type') == 'product' and set(target) == {'type', 'productId'} and isinstance(target.get('productId'), str):
        return db.execute(catalog_query().where(Product.id == target['productId'], Product.is_active, Category.is_visible)).first() is not None
    return False


def validate_target(db, target):
    if not is_target_available(db, target):
        raise ApiError(422, 'INVALID_TARGET', 'Выберите доступную категорию, товар или раздел сайта.')


def validate_media_ids(db, ids):
    from .content import Media

    if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ApiError(422, 'INVALID_MEDIA', 'Выберите не более восьми разных изображений.')
    ready = set(db.scalars(select(Media.id).where(Media.id.in_(ids), Media.state == 'ready'))) if ids else set()
    if len(ready) != len(ids):
        raise ApiError(422, 'INVALID_MEDIA', 'Выбранное изображение не найдено или ещё не готово.')


def search_conditions(query, *, admin=False):
    query = ' '.join(query.split())
    conditions = [column.icontains(query, autoescape=True) for column in [Product.sku, Product.name, Product.brand]]
    normalized = normalize_search(query)
    if normalized:
        conditions.append(Product.search_normalized.contains(normalized, autoescape=True))
    if admin:
        conditions.extend(column.icontains(query, autoescape=True) for column in [Product.id, Product.external_id])
    else:
        conditions.append(ProductPresentation.website_title.icontains(query, autoescape=True))
    return or_(*conditions)


def get_product_rows(db, product_id):
    result = db.execute(catalog_query().where(Product.id == product_id)).first()
    if result is None:
        raise ApiError(404, 'NOT_FOUND', 'Товар не найден.')
    return result


def set_version_headers(response, version):
    response.headers['ETag'] = f'"{version}"'
    response.headers['Cache-Control'] = 'no-store'


def commit_category(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, 'CATEGORY_EXISTS', 'Категория с таким названием уже существует.') from None


router = APIRouter(prefix='/api')


@router.get('/categories')
def list_public_categories(response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return [public_category(row) for row in db.scalars(select(Category).where(Category.is_visible).order_by(Category.position, Category.id))]


@router.get('/catalog')
def list_products(q: str = Query(default='', max_length=120, pattern=r'^[^\x00]*$'), category: Id | None = None, brand: str = Query(default='', max_length=80, pattern=r'^[^\x00]*$'),
                  inStock: bool = False, sort: Literal['relevance', 'price_asc', 'price_desc'] = 'relevance',
                  offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=24, ge=1, le=100), db: Session = Depends(get_db)):
    statement = catalog_query().where(Product.is_active, Category.is_visible)
    if q.strip():
        statement = statement.where(search_conditions(q))
    if category:
        statement = statement.where(Category.id == category)
    if brand:
        statement = statement.where(func.lower(Product.brand) == brand.lower())
    if inStock:
        statement = statement.where(Product.stock > Product.reserved)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    if sort == 'price_asc':
        statement = statement.order_by(Product.price_kopecks.asc(), Product.id)
    elif sort == 'price_desc':
        statement = statement.order_by(Product.price_kopecks.desc(), Product.id)
    else:
        if normalize_search(q):
            statement = statement.order_by(case((Product.search_normalized.startswith(normalize_search(q) + ' ', autoescape=True), 0), else_=1))
        statement = statement.order_by(Product.name, Product.id)
    rows = db.execute(statement.offset(offset).limit(limit)).all()
    return page_response([public_product(db, *row) for row in rows], total, offset, limit, **catalog_metadata(db))


@router.get('/catalog/{productId}')
def get_public_product(productId: Id = Path(min_length=1, max_length=128), db: Session = Depends(get_db)):
    row = get_product_rows(db, productId)
    if not row.Product.is_active or not row.Category.is_visible:
        raise ApiError(404, 'NOT_FOUND', 'Товар не найден.')
    return {'product': public_product(db, *row), **catalog_metadata(db)}


@router.get('/admin/categories')
def list_admin_categories(response: Response, offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=24, ge=1, le=100),
                          db: Session = Depends(get_db), staff=Depends(require_admin)):
    response.headers['Cache-Control'] = 'no-store'
    total = db.scalar(select(func.count()).select_from(Category))
    rows = db.scalars(select(Category).order_by(Category.position, Category.id).offset(offset).limit(limit))
    return page_response([admin_category(row) for row in rows], total, offset, limit)


@router.post('/admin/categories', status_code=201)
def create_category(body: CategoryCreate, response: Response, db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    category = Category(id=new_id(), name=body.name, normalized_name=body.name.casefold(), position=body.position,
                        is_visible=body.isVisible, show_in_menu=body.showInMenu, version=1)
    db.add(category)
    record_audit(db, staff, 'category.create', category.id, category.version)
    commit_category(db)
    set_version_headers(response, category.version)
    return admin_category(category)


@router.patch('/admin/categories/{categoryId}')
def update_category(body: CategoryPatch, response: Response, categoryId: Id = Path(min_length=1, max_length=128),
                    if_match: str | None = Header(default=None, alias='If-Match'), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    category = db.scalar(select(Category).where(Category.id == categoryId).with_for_update())
    if category is None:
        raise ApiError(404, 'NOT_FOUND', 'Категория не найдена.')
    check_version(category, if_match)
    fields = {'name': 'name', 'position': 'position', 'isVisible': 'is_visible', 'showInMenu': 'show_in_menu'}
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(category, fields[key], value)
    category.normalized_name = category.name.casefold()
    category.version += 1
    record_audit(db, staff, 'category.update', category.id, category.version)
    commit_category(db)
    set_version_headers(response, category.version)
    return admin_category(category)


@router.get('/admin/products')
def list_admin_products(response: Response, q: str = Query(default='', max_length=120, pattern=r'^[^\x00]*$'), category: Id | None = None,
                        offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=24, ge=1, le=100),
                        db: Session = Depends(get_db), staff=Depends(require_admin)):
    response.headers['Cache-Control'] = 'no-store'
    statement = catalog_query()
    if q.strip():
        statement = statement.where(search_conditions(q, admin=True))
    if category:
        statement = statement.where(Category.id == category)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(statement.order_by(Product.name, Product.id).offset(offset).limit(limit)).all()
    return page_response([admin_product(*row) for row in rows], total, offset, limit)


@router.get('/admin/products/{productId}/presentation')
def get_presentation(response: Response, productId: Id = Path(min_length=1, max_length=128), db: Session = Depends(get_db), staff=Depends(require_admin)):
    product, presentation, category = get_product_rows(db, productId)
    set_version_headers(response, presentation.version)
    return {'product': public_product(db, product, presentation, category), 'presentation': presentation_response(presentation)}


@router.patch('/admin/products/{productId}/presentation')
def update_presentation(body: PresentationPatch, response: Response, productId: Id = Path(min_length=1, max_length=128),
                        if_match: str | None = Header(default=None, alias='If-Match'), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    product = db.scalar(select(Product).where(Product.id == productId).with_for_update())
    if product is None:
        raise ApiError(404, 'NOT_FOUND', 'Товар не найден.')
    presentation = db.scalar(select(ProductPresentation).where(ProductPresentation.product_id == productId).with_for_update())
    if presentation is None:
        raise ApiError(404, 'NOT_FOUND', 'Оформление товара не найдено.')
    check_version(presentation, if_match)
    if 'categoryId' in body.model_fields_set and db.get(Category, body.categoryId) is None:
        raise ApiError(422, 'INVALID_CATEGORY', 'Категория не найдена.')
    if 'imageIds' in body.model_fields_set:
        validate_media_ids(db, body.imageIds)
    fields = {'categoryId': 'category_id', 'websiteTitle': 'website_title', 'websiteDescription': 'website_description', 'imageIds': 'image_ids'}
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(presentation, fields[key], value)
    presentation.version += 1
    record_audit(db, staff, 'product.presentation.update', productId, presentation.version)
    db.commit()
    set_version_headers(response, presentation.version)
    return presentation_response(presentation)
