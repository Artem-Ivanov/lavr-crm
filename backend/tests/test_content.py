from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture(autouse=True)
def site_configuration(db):
    from app.content import SiteConfig
    db.add(SiteConfig(id=1, version=1, menu=[],
                      hero={'title': 'Автозапчасти', 'description': 'Для вашего автомобиля',
                            'imageId': None, 'buttonText': 'Каталог', 'target': {'type': 'catalog'}},
                      settings={'storeName': 'ЛАВР', 'phone': '+79990000000', 'address': 'Смоленск',
                                'hours': 'Пн–Пт 09:00–18:00', 'categoriesTitle': 'Категории',
                                'promotionsTitle': 'Акции и объявления', 'catalogTitle': 'Каталог'}))
    db.commit()


def image_bytes(size=(8, 6), image_format='PNG'):
    output = BytesIO()
    with Image.new('RGB', size, '#fa551e') as image:
        image.save(output, format=image_format)
    return output.getvalue()


def upload(client, headers, data=None, mime='image/png', alt='Описание', filename='photo.png'):
    return client.post('/api/admin/media', headers=headers,
                       files={'file': (filename, image_bytes() if data is None else data, mime)},
                       data={'alt': alt})


def publication(**overrides):
    return {'kind': 'offer', 'title': 'Скидка на фильтры', 'description': 'Обычный текст',
            'imageIds': [], 'target': {'type': 'catalog'}, 'buttonText': 'Открыть каталог',
            'position': 0, **overrides}


def test_site_patch_keeps_omitted_sections_and_rejects_stale_version(client, admin_headers):
    before = client.get('/api/admin/site').json()
    changed = {**before['settings'], 'storeName': 'Новое название'}
    headers = {**admin_headers, 'If-Match': f'"{before["version"]}"'}
    saved = client.patch('/api/admin/site', headers=headers, json={'settings': changed, 'menu': []})
    assert saved.status_code == 200
    assert saved.headers['etag'] == f'"{before["version"] + 1}"'
    assert saved.json()['hero'] == before['hero']
    assert client.get('/api/site').json()['menu'] == []
    stale = client.patch('/api/admin/site', headers=headers, json={'settings': before['settings']})
    assert stale.status_code == 412
    assert client.get('/api/site').json()['settings']['storeName'] == 'Новое название'


@pytest.mark.parametrize('patch', [{}, {'version': 2}, {'menu': None},
                                   {'menu': [{'label': 'x', 'target': {'type': 'catalog', 'url': 'https://evil.test'},
                                              'position': 0, 'isVisible': True}]}])
def test_site_rejects_unknown_or_incomplete_fields(client, admin_headers, patch):
    before = client.get('/api/admin/site').json()
    response = client.patch('/api/admin/site', headers={**admin_headers, 'If-Match': f'"{before["version"]}"'}, json=patch)
    assert response.status_code == 422
    assert client.get('/api/admin/site').json() == before


def test_content_requires_admin_and_csrf(client, admin_headers, manager_headers):
    client.cookies.clear()
    assert client.get('/api/admin/site').status_code == 401
    assert client.post('/api/admin/media', files={'file': ('bad.svg', b'<svg/>', 'image/svg+xml')}, data={'alt': ''}).status_code == 401
    assert client.get('/api/admin/publications', headers=manager_headers).status_code == 403
    assert client.get('/api/admin/media', headers=manager_headers).status_code == 403
    unsafe = {key: value for key, value in admin_headers.items() if key.lower() != 'x-csrf-token'}
    assert client.post('/api/admin/publications', headers=unsafe, json=publication()).status_code == 403


def test_publication_lifecycle_and_private_image_access(client, admin_headers):
    media = upload(client, admin_headers, filename='../../outside.png').json()
    assert media['path'] == f'/api/admin/media/{media["id"]}/file'
    assert client.get(media['path'], headers=admin_headers).content == image_bytes()
    assert client.get(f'/api/media/{media["id"]}').status_code == 404
    created = client.post('/api/admin/publications', headers=admin_headers,
                          json=publication(imageIds=[media['id']]))
    assert created.status_code == 201
    item = created.json()
    assert item['status'] == 'draft' and item['version'] == 1
    assert item['id'] not in [row['id'] for row in client.get('/api/site').json()['publications']]
    saved = client.patch(f'/api/admin/publications/{item["id"]}',
                         headers={**admin_headers, 'If-Match': '"1"'}, json={'status': 'published'})
    assert saved.status_code == 200
    public = next(row for row in client.get('/api/site').json()['publications'] if row['id'] == item['id'])
    assert set(public) == {'id', 'kind', 'title', 'description', 'images', 'target', 'buttonText', 'position'}
    assert public['images'][0]['path'] == f'/api/media/{media["id"]}'
    assert client.get(public['images'][0]['path']).content == image_bytes()
    assert client.get(public['images'][0]['path']).headers['cache-control'] == 'no-store'
    archived = client.patch(f'/api/admin/publications/{item["id"]}',
                            headers={**admin_headers, 'If-Match': '"2"'}, json={'status': 'archived'})
    assert archived.status_code == 200
    assert client.get(public['images'][0]['path']).status_code == 404


def test_publication_window_uses_server_instants_and_combined_patch(client, admin_headers, monkeypatch):
    from app import content
    starts = '2026-10-12T12:00:00+03:00'
    ends = '2026-10-12T09:01:00Z'
    created = client.post('/api/admin/publications', headers=admin_headers,
                          json=publication(status='published', startsAt=starts, endsAt=ends))
    assert created.status_code == 201
    item = created.json()
    monkeypatch.setattr(content, 'utcnow', lambda: datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc))
    assert item['id'] in [row['id'] for row in client.get('/api/site').json()['publications']]
    monkeypatch.setattr(content, 'utcnow', lambda: datetime(2026, 10, 12, 9, 1, tzinfo=timezone.utc))
    assert item['id'] not in [row['id'] for row in client.get('/api/site').json()['publications']]
    invalid = client.patch(f'/api/admin/publications/{item["id"]}',
                           headers={**admin_headers, 'If-Match': '"1"'}, json={'startsAt': ends})
    assert invalid.status_code == 422
    assert client.get(f'/api/admin/publications/{item["id"]}', headers=admin_headers).json()['version'] == 1


@pytest.mark.parametrize('patch', [{'title': '   '}, {'position': True}, {'imageIds': ['x', 'x']},
                                   {'startsAt': '2026-10-12T12:00:00'}, {'startsAt': 0},
                                   {'startsAt': '2026-02-30T12:00:00Z'},
                                   {'startsAt': '2026-10-12T12:00:00+03:99'},
                                   {'description': 'Текст\x00описания'}, {'unexpected': 'value'}])
def test_publication_validates_real_closed_input(client, admin_headers, patch):
    assert client.post('/api/admin/publications', headers=admin_headers, json=publication(**patch)).status_code == 422


@pytest.mark.parametrize('data,mime,expected', [(b'<svg/>', 'image/svg+xml', 415),
                                               (b'<svg/>', 'image/png', 415),
                                               (image_bytes(), 'image/jpeg', 415),
                                               (image_bytes()[:20], 'image/png', 415),
                                               (b'x' * (5242880 + 1), 'image/png', 413),
                                               (image_bytes((4097, 1)), 'image/png', 422)],
                         ids=['svg', 'spoofed-png', 'mismatched-mime', 'truncated', 'too-large', 'too-wide'])
def test_upload_confirms_actual_bytes_format_and_dimensions(client, admin_headers, data, mime, expected):
    assert upload(client, admin_headers, data=data, mime=mime).status_code == expected


def test_failed_media_metadata_write_removes_new_file(client, admin_headers, monkeypatch):
    from app.config import settings
    from app.content import Media
    from sqlalchemy.orm import Session
    before = set(settings.media_dir.iterdir())
    original_commit = Session.commit

    def fail_media_commit(session):
        if any(isinstance(row, Media) for row in session.new):
            raise SQLAlchemyError('metadata write failed')
        return original_commit(session)

    monkeypatch.setattr(Session, 'commit', fail_media_commit)
    response = upload(client, admin_headers)
    assert response.status_code == 503
    assert set(settings.media_dir.iterdir()) == before


def test_publication_and_media_lists_are_filtered_and_paged(client, admin_headers):
    for title in ('Первая', 'Вторая', 'Третья'):
        assert client.post('/api/admin/publications', headers=admin_headers,
                           json=publication(title=title, kind='announcement')).status_code == 201
    page = client.get('/api/admin/publications?kind=announcement&status=draft&offset=1&limit=1', headers=admin_headers).json()
    assert page['total'] == 3 and len(page['items']) == 1
    assert page['offset'] == 1 and page['limit'] == 1
    assert client.get('/api/admin/publications?limit=101', headers=admin_headers).status_code == 422
    assert client.get('/api/admin/media?limit=1', headers=admin_headers).json()['limit'] == 1


def test_content_rejects_database_null_character_and_unsafe_offsets(client, admin_headers):
    assert upload(client, admin_headers, alt='Описание\x00изображения').status_code == 422
    before = client.get('/api/admin/site').json()
    bad_hero = {**before['hero'], 'description': 'Описание\x00баннера'}
    assert client.patch('/api/admin/site', headers={**admin_headers, 'If-Match': '"1"'},
                        json={'hero': bad_hero}).status_code == 422
    for path in ['/api/admin/publications', '/api/admin/media']:
        assert client.get(path + '?offset=9223372036854775808', headers=admin_headers).status_code == 422


def test_hidden_targets_revoke_publication_media_but_keep_visible_hero_image(client, admin_headers):
    category = client.post('/api/admin/categories', headers=admin_headers,
                           json={'name': 'Фильтры', 'position': 0, 'isVisible': True, 'showInMenu': True}).json()
    media = upload(client, admin_headers).json()
    before = client.get('/api/admin/site').json()
    target = {'type': 'category', 'categoryId': category['id']}
    hero = {**before['hero'], 'imageId': media['id'], 'target': target}
    assert client.patch('/api/admin/site', headers={**admin_headers, 'If-Match': '"1"'},
                        json={'hero': hero, 'menu': [{'label': 'Фильтры', 'target': target,
                                                     'position': 0, 'isVisible': True}]}).status_code == 200
    item = client.post('/api/admin/publications', headers=admin_headers,
                       json=publication(status='published', imageIds=[media['id']], target=target)).json()
    assert client.patch(f'/api/admin/categories/{category["id"]}', headers={**admin_headers, 'If-Match': '"1"'},
                        json={'isVisible': False}).status_code == 200
    public = client.get('/api/site').json()
    assert public['hero']['target'] is None and public['hero']['image']['id'] == media['id']
    assert public['menu'] == [] and item['id'] not in [row['id'] for row in public['publications']]
    assert client.get(f'/api/media/{media["id"]}').status_code == 200
    assert client.patch('/api/admin/site', headers={**admin_headers, 'If-Match': '"2"'},
                        json={'hero': {**hero, 'imageId': None, 'target': {'type': 'catalog'}}}).status_code == 200
    assert client.get(f'/api/media/{media["id"]}').status_code == 404
    assert client.patch(f'/api/admin/publications/{item["id"]}', headers={**admin_headers, 'If-Match': '"1"'},
                        json={'status': 'archived'}).status_code == 200


def test_product_media_requires_both_active_product_and_visible_category(client, admin_headers, db):
    from app.catalog import Category, Product, ProductPresentation
    from app.common import utcnow
    media = upload(client, admin_headers).json()
    category = Category(id='media-category', name='Фильтры', normalized_name='фильтры',
                        position=0, is_visible=True, show_in_menu=True, version=1)
    product = Product(id='media-product', external_id='1c-media-product', sku='W914', brand='MANN',
                      name='Фильтр', price_kopecks=10000, stock=5, reserved=0,
                      is_active=True, delivery_days=0, updated_at=utcnow())
    db.add_all([category, product])
    db.flush()
    db.add(ProductPresentation(product_id=product.id, category_id=category.id, image_ids=[media['id']], version=1))
    db.commit()
    path = f'/api/media/{media["id"]}'
    assert client.get(path).status_code == 200
    product.is_active = False
    db.commit()
    assert client.get(path).status_code == 404
    product.is_active = True
    category.is_visible = False
    db.commit()
    assert client.get(path).status_code == 404


def test_draft_can_keep_hidden_target_but_cannot_publish_it(client, admin_headers):
    category = client.post('/api/admin/categories', headers=admin_headers,
                           json={'name': 'Скрытые фильтры', 'position': 0, 'isVisible': False, 'showInMenu': False}).json()
    draft = client.post('/api/admin/publications', headers=admin_headers,
                        json=publication(target={'type': 'category', 'categoryId': category['id']}))
    assert draft.status_code == 201
    assert client.patch(f'/api/admin/publications/{draft.json()["id"]}',
                        headers={**admin_headers, 'If-Match': '"1"'}, json={'status': 'published'}).status_code == 422
    assert client.post('/api/admin/publications', headers=admin_headers,
                       json=publication(target={'type': 'category', 'categoryId': 'does-not-exist'})).status_code == 422


@pytest.mark.parametrize('identifier', ['invalid%00id', 'invalid%1Fid', 'x' * 129],
                         ids=['null-character', 'control-character', 'too-long'])
def test_content_path_identifiers_are_validated_before_database_queries(client, admin_headers, identifier):
    with TestClient(client.app, base_url='http://testserver', raise_server_exceptions=False) as requests:
        for path in [f'/api/admin/publications/{identifier}', f'/api/admin/media/{identifier}/file', f'/api/media/{identifier}']:
            response = requests.get(path, headers=admin_headers)
            assert response.status_code == 422, response.text
        assert requests.patch(f'/api/admin/publications/{identifier}',
                              headers={**admin_headers, 'If-Match': '"1"'}, json={'status': 'archived'}).status_code == 422
