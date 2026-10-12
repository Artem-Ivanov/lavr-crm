import os
import re
import tempfile
import warnings
from datetime import datetime
from io import BytesIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator
from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String, Text, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .auth import record_audit, require_admin, require_admin_mutation
from .common import ApiError, check_version, new_id, page_response, utcnow
from .config import settings
from .db import Base, get_db


router = APIRouter(prefix='/api')
MAX_FILE_BYTES = 5242880


class SiteConfig(Base):
    __tablename__ = 'site_configuration'
    __table_args__ = (CheckConstraint('id = 1'), CheckConstraint('version BETWEEN 1 AND 9007199254740991'))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    hero: Mapped[dict] = mapped_column(JSONB)
    settings: Mapped[dict] = mapped_column(JSONB)
    menu: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class Publication(Base):
    __tablename__ = 'publications'
    __table_args__ = (CheckConstraint("kind IN ('offer', 'announcement', 'hot')"),
                      CheckConstraint("status IN ('draft', 'published', 'archived')"),
                      CheckConstraint('position >= 0 AND position <= 1000000'),
                      CheckConstraint('version BETWEEN 1 AND 9007199254740991'),
                      CheckConstraint('starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at'))

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default='')
    image_ids: Mapped[list] = mapped_column(JSONB, default=list)
    target: Mapped[dict] = mapped_column(JSONB)
    button_text: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='draft', index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class Media(Base):
    __tablename__ = 'media'
    __table_args__ = (CheckConstraint("state = 'ready'"),
                      CheckConstraint('size_bytes > 0 AND size_bytes <= 5242880'),
                      CheckConstraint('width > 0 AND width <= 4096 AND height > 0 AND height <= 4096'))

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(160), unique=True)
    mime_type: Mapped[str] = mapped_column(String(20))
    alt: Mapped[str] = mapped_column(String(240), default='')
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    state: Mapped[str] = mapped_column(String(10), default='ready')


def nonblank(value):
    if not value.strip():
        raise ValueError('Поле не может состоять из пробелов.')
    return value


def identifier(value):
    if value in ('.', '..') or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('Некорректный идентификатор.')
    return value


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value, re.IGNORECASE):
        raise ValueError('Укажите дату, время и часовой пояс.')
    offset = re.search(r'[+-](\d{2}):(\d{2})$', value)
    if offset and (int(offset[1]) >= 24 or int(offset[2]) >= 60):
        raise ValueError('Некорректный часовой пояс.')
    try:
        return datetime.fromisoformat(value.upper().replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Некорректная дата.') from error


def unique_ids(value):
    if len(value) != len(set(value)):
        raise ValueError('Изображения не должны повторяться.')
    return value


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(identifier)]
PathIdentifier = Annotated[StrictStr, Path(min_length=1, max_length=128), AfterValidator(identifier)]
Nonblank = Annotated[StrictStr, AfterValidator(nonblank)]
Position = Annotated[StrictInt, Field(ge=0, le=1000000)]
ImageIds = Annotated[list[Identifier], Field(max_length=8), AfterValidator(unique_ids)]
Timestamp = Annotated[datetime, BeforeValidator(timestamp)]
Kind = Literal['offer', 'announcement', 'hot']
Status = Literal['draft', 'published', 'archived']


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*')
    @classmethod
    def database_text(cls, value):
        if isinstance(value, str) and '\x00' in value:
            raise ValueError('Недопустимый символ в тексте.')
        return value


class CatalogTarget(ClosedModel):
    type: Literal['catalog']


class CategoryTarget(ClosedModel):
    type: Literal['category']
    categoryId: Identifier


class ProductTarget(ClosedModel):
    type: Literal['product']
    productId: Identifier


class SectionTarget(ClosedModel):
    type: Literal['section']
    section: Literal['offers', 'announcements', 'hot']


Target = Annotated[CatalogTarget | CategoryTarget | ProductTarget | SectionTarget, Field(discriminator='type')]


class HeroInput(ClosedModel):
    title: Nonblank = Field(min_length=1, max_length=240)
    description: StrictStr = Field(max_length=2000)
    imageId: Identifier | None
    buttonText: Nonblank = Field(min_length=1, max_length=80)
    target: Target


class SettingsInput(ClosedModel):
    storeName: Nonblank = Field(min_length=1, max_length=120)
    phone: Nonblank = Field(min_length=10, max_length=30)
    address: Nonblank = Field(min_length=1, max_length=500)
    hours: Nonblank = Field(min_length=1, max_length=240)
    categoriesTitle: Nonblank = Field(min_length=1, max_length=120)
    promotionsTitle: Nonblank = Field(min_length=1, max_length=120)
    catalogTitle: Nonblank = Field(min_length=1, max_length=120)


class MenuItemInput(ClosedModel):
    label: Nonblank = Field(min_length=1, max_length=80)
    target: Target
    position: Position
    isVisible: StrictBool


class SitePatch(ClosedModel):
    hero: HeroInput | None = None
    settings: SettingsInput | None = None
    menu: list[MenuItemInput] | None = Field(default=None, max_length=12)

    @model_validator(mode='after')
    def nonempty_patch(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Передайте хотя бы одну полную секцию сайта.')
        return self


class PublicationInput(ClosedModel):
    kind: Kind
    title: Nonblank = Field(min_length=1, max_length=240)
    description: StrictStr = Field(max_length=4000)
    imageIds: ImageIds
    target: Target
    buttonText: Nonblank = Field(min_length=1, max_length=80)
    position: Position
    status: Status = 'draft'
    startsAt: Timestamp | None = None
    endsAt: Timestamp | None = None

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.startsAt is not None and self.endsAt is not None and self.startsAt >= self.endsAt:
            raise ValueError('Начало показа должно быть раньше окончания.')
        return self


class PublicationPatch(ClosedModel):
    kind: Kind | None = None
    title: Nonblank | None = Field(default=None, min_length=1, max_length=240)
    description: StrictStr | None = Field(default=None, max_length=4000)
    imageIds: ImageIds | None = None
    target: Target | None = None
    buttonText: Nonblank | None = Field(default=None, min_length=1, max_length=80)
    position: Position | None = None
    status: Status | None = None
    startsAt: Timestamp | None = None
    endsAt: Timestamp | None = None

    @model_validator(mode='after')
    def nonempty_patch(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set - {'startsAt', 'endsAt'}):
            raise ValueError('Передайте хотя бы одно поле публикации.')
        return self


def public_media(row):
    return {'id': row.id, 'path': f'/api/media/{row.id}', 'alt': row.alt,
            'width': row.width, 'height': row.height}


def private_media(row):
    return {**public_media(row), 'path': f'/api/admin/media/{row.id}/file',
            'mimeType': row.mime_type, 'sizeBytes': row.size_bytes,
            'state': row.state, 'createdAt': row.created_at.isoformat()}


def site_configuration(row):
    return {'hero': row.hero, 'settings': row.settings, 'menu': row.menu, 'version': row.version}


def publication_dto(row):
    return {'id': row.id, 'kind': row.kind, 'title': row.title, 'description': row.description,
            'imageIds': row.image_ids, 'target': row.target, 'buttonText': row.button_text,
            'position': row.position, 'status': row.status,
            'startsAt': row.starts_at.isoformat() if row.starts_at else None,
            'endsAt': row.ends_at.isoformat() if row.ends_at else None, 'version': row.version}


def ready_media(db, ids):
    if not ids:
        return []
    rows = {row.id: row for row in db.scalars(select(Media).where(Media.id.in_(ids), Media.state == 'ready'))}
    return [public_media(rows[item_id]) for item_id in ids if item_id in rows]


def active_publications(now):
    return select(Publication).where(Publication.status == 'published',
                                     or_(Publication.starts_at.is_(None), Publication.starts_at <= now),
                                     or_(Publication.ends_at.is_(None), now < Publication.ends_at))


def get_site_row(db, *, lock=False):
    query = select(SiteConfig).where(SiteConfig.id == 1)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(503, 'SITE_UNAVAILABLE', 'Настройки сайта ещё не подготовлены.')
    return row


def version_response(response, row):
    response.headers['ETag'] = f'"{row.version}"'
    response.headers['Cache-Control'] = 'no-store'


def validate_references(db, values, *, publication=False):
    from .catalog import Category, Product, validate_media_ids, validate_target

    if 'target' in values:
        target = values['target']
        if publication and values['status'] != 'published':
            model, key = {'category': (Category, 'categoryId'), 'product': (Product, 'productId')}.get(target['type'], (None, None))
            if model is not None and db.get(model, target[key]) is None:
                raise ApiError(422, 'TARGET_NOT_FOUND', 'Указанная цель не найдена.')
        else:
            validate_target(db, target)
    if 'imageIds' in values:
        validate_media_ids(db, values['imageIds'])


@router.get('/site')
def get_public_site(response: Response, db: Session = Depends(get_db)):
    from .catalog import is_target_available

    row = get_site_row(db)
    hero = {key: row.hero[key] for key in ('title', 'description', 'buttonText', 'target')}
    images = ready_media(db, [row.hero['imageId']] if row.hero['imageId'] else [])
    hero['image'] = images[0] if images else None
    if not is_target_available(db, hero['target']):
        hero['target'] = None
    publications = []
    for item in db.scalars(active_publications(utcnow()).order_by(Publication.position, Publication.id)):
        if is_target_available(db, item.target):
            publications.append({'id': item.id, 'kind': item.kind, 'title': item.title,
                                 'description': item.description, 'images': ready_media(db, item.image_ids),
                                 'target': item.target, 'buttonText': item.button_text, 'position': item.position})
    menu = [item for item in sorted(row.menu, key=lambda value: value['position'])
            if item['isVisible'] and is_target_available(db, item['target'])]
    response.headers['Cache-Control'] = 'no-store'
    return {'hero': hero, 'settings': row.settings, 'menu': menu, 'publications': publications}


@router.get('/admin/site')
def get_admin_site(response: Response, db: Session = Depends(get_db), staff=Depends(require_admin)):
    row = get_site_row(db)
    version_response(response, row)
    return site_configuration(row)


@router.patch('/admin/site')
def update_site(body: SitePatch, response: Response, if_match: str | None = Header(default=None),
                db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    from .catalog import validate_media_ids

    row = get_site_row(db, lock=True)
    check_version(row, if_match)
    patch = body.model_dump(exclude_unset=True)
    if 'hero' in patch:
        validate_references(db, patch['hero'])
        validate_media_ids(db, [patch['hero']['imageId']] if patch['hero']['imageId'] else [])
    for item in patch.get('menu', []):
        validate_references(db, item)
    for key, value in patch.items():
        setattr(row, key, value)
    row.version += 1
    record_audit(db, staff, 'site.update', str(row.id), row.version)
    db.commit()
    version_response(response, row)
    return site_configuration(row)


@router.get('/admin/publications')
def list_publications(response: Response, kind: Kind | None = None, status: Status | None = None,
                      offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=24, ge=1, le=100),
                      db: Session = Depends(get_db), staff=Depends(require_admin)):
    query = select(Publication)
    if kind is not None:
        query = query.where(Publication.kind == kind)
    if status is not None:
        query = query.where(Publication.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Publication.position, Publication.id).offset(offset).limit(limit))
    response.headers['Cache-Control'] = 'no-store'
    return page_response([publication_dto(item) for item in items], total, offset, limit)


def apply_publication(row, values):
    names = {'imageIds': 'image_ids', 'buttonText': 'button_text', 'startsAt': 'starts_at', 'endsAt': 'ends_at'}
    for key, value in values.items():
        setattr(row, names.get(key, key), value)


@router.post('/admin/publications', status_code=201)
def create_publication(body: PublicationInput, response: Response,
                       db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    values = body.model_dump()
    validate_references(db, values, publication=True)
    row = Publication(id=new_id(), version=1)
    apply_publication(row, values)
    db.add(row)
    record_audit(db, staff, 'publication.create', row.id, row.version)
    db.commit()
    version_response(response, row)
    return publication_dto(row)


def get_publication_row(db, publication_id, *, lock=False):
    query = select(Publication).where(Publication.id == publication_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, 'PUBLICATION_NOT_FOUND', 'Публикация не найдена.')
    return row


@router.get('/admin/publications/{publication_id}')
def get_admin_publication(publication_id: PathIdentifier, response: Response,
                          db: Session = Depends(get_db), staff=Depends(require_admin)):
    row = get_publication_row(db, publication_id)
    version_response(response, row)
    return publication_dto(row)


@router.patch('/admin/publications/{publication_id}')
def update_publication(publication_id: PathIdentifier, body: PublicationPatch, response: Response,
                       if_match: str | None = Header(default=None),
                       db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    row = get_publication_row(db, publication_id, lock=True)
    check_version(row, if_match)
    values = {key: value for key, value in publication_dto(row).items() if key not in ('id', 'version')}
    values.update(body.model_dump(mode='json', exclude_unset=True))
    try:
        values = PublicationInput.model_validate(values).model_dump()
    except ValueError as error:
        raise ApiError(422, 'INVALID_PUBLICATION', 'Проверьте поля публикации и период показа.') from error
    validate_references(db, values, publication=True)
    apply_publication(row, values)
    row.version += 1
    record_audit(db, staff, 'publication.update', row.id, row.version)
    db.commit()
    version_response(response, row)
    return publication_dto(row)


@router.get('/admin/media')
def list_media(response: Response, offset: int = Query(default=0, ge=0, le=9007199254740991),
               limit: int = Query(default=24, ge=1, le=100),
               db: Session = Depends(get_db), staff=Depends(require_admin)):
    query = select(Media).where(Media.state == 'ready')
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Media.created_at.desc(), Media.id.desc()).offset(offset).limit(limit))
    response.headers['Cache-Control'] = 'no-store'
    return page_response([private_media(row) for row in rows], total, offset, limit)


def decode_image(data, mime_type):
    formats = {'image/jpeg': ('JPEG', 'jpg'), 'image/png': ('PNG', 'png'), 'image/webp': ('WEBP', 'webp')}
    signature = ((mime_type == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')) or
                 (mime_type == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or
                 (mime_type == 'image/webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP'))
    if mime_type not in formats or not signature:
        raise ApiError(415, 'UNSUPPORTED_IMAGE', 'Выберите корректное изображение JPG, PNG или WebP.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != formats[mime_type][0]:
                    raise ApiError(415, 'UNSUPPORTED_IMAGE', 'Содержимое файла не соответствует формату.')
                width, height = image.size
                if not (0 < width <= 4096 and 0 < height <= 4096):
                    raise ApiError(422, 'IMAGE_DIMENSIONS', 'Размеры изображения: не более 4096 × 4096.')
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
        return width, height, formats[mime_type][1]
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ApiError(422, 'IMAGE_DIMENSIONS', 'Изображение превышает допустимые размеры.') from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ApiError(415, 'INVALID_IMAGE', 'Файл повреждён или не является изображением.') from error


def persist_media(db, staff, data, mime_type, alt):
    width, height, extension = decode_image(data, mime_type)
    media_id = new_id()
    filename = f'{media_id}.{extension}'
    destination = settings.media_dir / filename
    temporary = None
    saved = False
    try:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=settings.media_dir, prefix='.upload-', delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        saved = True
        os.unlink(temporary)
        temporary = None
        row = Media(id=media_id, filename=filename, mime_type=mime_type, alt=alt,
                    width=width, height=height, size_bytes=len(data), created_at=utcnow(), state='ready')
        db.add(row)
        record_audit(db, staff, 'media.create', media_id)
        db.commit()
        return private_media(row)
    except (OSError, SQLAlchemyError) as error:
        db.rollback()
        if saved:
            destination.unlink(missing_ok=True)
        raise ApiError(503, 'MEDIA_STORAGE_UNAVAILABLE', 'Не удалось сохранить изображение. Попробуйте ещё раз.') from error
    finally:
        if temporary is not None:
            os.unlink(temporary)


@router.post('/admin/media', status_code=201)
async def upload_media(request: Request, response: Response,
                       db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    if not request.headers.get('content-type', '').lower().startswith('multipart/form-data;'):
        raise ApiError(415, 'UNSUPPORTED_UPLOAD', 'Загрузите файл в формате multipart/form-data.')
    async with request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
        if len(form.multi_items()) != 2 or set(form.keys()) != {'file', 'alt'}:
            raise ApiError(422, 'INVALID_UPLOAD', 'Передайте изображение и его альтернативный текст.')
        file, alt = form['file'], form['alt']
        if not isinstance(file, UploadFile) or not isinstance(alt, str) or len(alt) > 240 or '\x00' in alt:
            raise ApiError(422, 'INVALID_UPLOAD', 'Альтернативный текст: максимум 240 символов.')
        if file.size is not None and file.size > MAX_FILE_BYTES:
            raise ApiError(413, 'IMAGE_TOO_LARGE', 'Размер изображения: не более 5 МиБ.')
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ApiError(413, 'IMAGE_TOO_LARGE', 'Размер изображения: не более 5 МиБ.')
        if not data:
            raise ApiError(415, 'INVALID_IMAGE', 'Выберите непустой файл изображения.')
        result = await run_in_threadpool(persist_media, db, staff, data, file.content_type, alt)
    response.headers['Cache-Control'] = 'no-store'
    return result


def is_media_public(db, media_id):
    from .catalog import Category, Product, ProductPresentation, is_target_available

    site = db.get(SiteConfig, 1)
    if site is not None and site.hero['imageId'] == media_id:
        return True
    for item in db.scalars(active_publications(utcnow()).where(Publication.image_ids.contains([media_id]))):
        if is_target_available(db, item.target):
            return True
    return db.scalar(select(ProductPresentation.product_id)
                     .join(Product, Product.id == ProductPresentation.product_id)
                     .join(Category, Category.id == ProductPresentation.category_id)
                     .where(Product.is_active.is_(True), Category.is_visible.is_(True),
                            ProductPresentation.image_ids.contains([media_id])).limit(1)) is not None


def media_file(db, media_id):
    row = db.get(Media, media_id)
    if row is None or row.state != 'ready' or not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:jpg|png|webp)', row.filename):
        raise ApiError(404, 'MEDIA_NOT_FOUND', 'Изображение не найдено.')
    path = settings.media_dir / row.filename
    if not path.is_file():
        raise ApiError(404, 'MEDIA_NOT_FOUND', 'Изображение не найдено.')
    return FileResponse(path, media_type=row.mime_type,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/media/{media_id}')
def get_public_media(media_id: PathIdentifier, db: Session = Depends(get_db)):
    if not is_media_public(db, media_id):
        raise ApiError(404, 'MEDIA_NOT_FOUND', 'Изображение не найдено.')
    return media_file(db, media_id)


@router.get('/admin/media/{media_id}/file')
def get_private_media(media_id: PathIdentifier, db: Session = Depends(get_db), staff=Depends(require_admin)):
    return media_file(db, media_id)
