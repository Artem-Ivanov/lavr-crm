import pytest


@pytest.fixture
def catalog_rows(db):
    from app.catalog import Category, Product, ProductPresentation
    from app.common import utcnow

    categories = [
        Category(id='cat-filters', name='Фильтры', normalized_name='фильтры', position=1, is_visible=True, show_in_menu=True, version=1),
        Category(id='cat-hidden', name='Скрытые', normalized_name='скрытые', position=0, is_visible=False, show_in_menu=True, version=1),
        Category(id='cat-oils', name='Масла', normalized_name='масла', position=2, is_visible=True, show_in_menu=False, version=1),
    ]
    products = [
        Product(id='p-visible', external_id='external-visible', sku='W 914/2', brand='MANN', name='Фильтр масляный', price_kopecks=150000, stock=5, reserved=1, is_active=True, delivery_days=2, updated_at=utcnow()),
        Product(id='p-hidden', external_id='external-hidden', sku='HIDDEN-1', brand='Hidden', name='Скрытый фильтр', price_kopecks=90000, stock=10, is_active=True, delivery_days=0, updated_at=utcnow()),
        Product(id='p-inactive', external_id='external-inactive', sku='INACTIVE-1', brand='Inactive', name='Удалённый фильтр', price_kopecks=80000, stock=10, is_active=False, delivery_days=0, updated_at=utcnow()),
        Product(id='p-empty', external_id='external-empty', sku='EMPTY-1', brand='Empty', name='Масло', price_kopecks=50000, stock=2, reserved=2, is_active=True, delivery_days=3, updated_at=utcnow()),
    ]
    db.add_all(categories + products)
    db.flush()
    for product, category in zip(products, ['cat-filters', 'cat-hidden', 'cat-filters', 'cat-oils']):
        db.add(ProductPresentation(product_id=product.id, category_id=category, website_title=None, website_description=None, image_ids=[], version=1))
    db.commit()
    return products


@pytest.mark.parametrize('query', ['w9142', 'W914/2', 'W 914 2'])
def test_public_search_normalizes_sku(client, catalog_rows, query):
    response = client.get('/api/catalog', params={'q': query})
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['items']] == ['p-visible']
    assert response.json()['items'][0]['stock'] == 4


def test_visibility_applies_to_search_direct_cards_and_categories(client, catalog_rows):
    response = client.get('/api/catalog')
    assert response.status_code == 200
    assert set(item['id'] for item in response.json()['items']) == {'p-visible', 'p-empty'}
    for product_id in ['p-hidden', 'p-inactive']:
        assert client.get(f'/api/catalog/{product_id}').status_code == 404
    categories = client.get('/api/categories').json()
    assert [item['id'] for item in categories] == ['cat-filters', 'cat-oils']
    assert categories[1]['showInMenu'] is False
    assert all(set(item) == {'id', 'name', 'position', 'showInMenu'} for item in categories)
    assert set(response.json()['items'][0]) == {'id', 'externalId', 'sku', 'brand', 'name', 'category', 'priceKopecks', 'stock', 'image', 'deliveryDays', 'websiteTitle', 'websiteDescription', 'images'}


def test_server_filters_sort_pagination_and_literal_search(client, catalog_rows):
    response = client.get('/api/catalog', params={'sort': 'price_asc', 'offset': 1, 'limit': 1})
    assert response.status_code == 200
    page = response.json()
    assert (page['total'], page['offset'], page['limit']) == (2, 1, 1)
    assert [item['id'] for item in page['items']] == ['p-visible']
    assert client.get('/api/catalog', params={'inStock': True}).json()['total'] == 1
    assert client.get('/api/catalog', params={'brand': 'mann', 'category': 'cat-filters'}).json()['total'] == 1
    assert client.get('/api/catalog', params={'q': '%'}).json()['total'] == 0
    for params in [{'limit': 101}, {'offset': -1}, {'sort': 'unknown'}, {'q': 'a' * 121}]:
        assert client.get('/api/catalog', params=params).status_code == 422


@pytest.mark.parametrize('params', [{'q': '\x00'}, {'brand': '\x00'}, {'offset': 9007199254740992}])
def test_catalog_query_rejects_unrepresentable_database_inputs(client, catalog_rows, params):
    assert client.get('/api/catalog', params=params).status_code == 422


def test_admin_search_finds_hidden_and_inactive_products(client, catalog_rows, admin_headers):
    for query, expected in [('external-hidden', 'p-hidden'), ('p-inactive', 'p-inactive'), ('HIDDEN1', 'p-hidden')]:
        response = client.get('/api/admin/products', params={'q': query}, headers=admin_headers)
        assert response.status_code == 200
        assert [item['id'] for item in response.json()['items']] == [expected]
        assert response.json()['items'][0]['isVisible'] is False
    response = client.get('/api/admin/products/p-hidden/presentation', headers=admin_headers)
    assert response.status_code == 200
    assert response.headers['etag'] == '"1"'
    assert response.json()['product']['image'] == ''


def test_categories_preserve_id_enforce_unique_name_and_version(client, catalog_rows, admin_headers):
    create = client.post('/api/admin/categories', json={'name': '  Новая   категория  ', 'position': 5, 'isVisible': True, 'showInMenu': False}, headers=admin_headers)
    assert create.status_code == 201
    created = create.json()
    assert created['name'] == 'Новая категория'
    assert created['version'] == 1
    assert create.headers['etag'] == '"1"'
    duplicate = client.post('/api/admin/categories', json={'name': 'НОВАЯ категория', 'position': 6, 'isVisible': True, 'showInMenu': True}, headers=admin_headers)
    assert duplicate.status_code == 409
    headers = {**admin_headers, 'If-Match': '"1"'}
    update = client.patch(f"/api/admin/categories/{created['id']}", json={'name': 'Переименована', 'isVisible': False}, headers=headers)
    assert update.status_code == 200
    assert update.json()['id'] == created['id']
    assert update.json()['version'] == 2
    assert update.json()['showInMenu'] is False
    assert update.headers['etag'] == '"2"'
    stale = client.patch(f"/api/admin/categories/{created['id']}", json={'position': 9}, headers=headers)
    assert stale.status_code == 412
    current = client.get('/api/admin/categories', headers=admin_headers).json()['items']
    assert next(item for item in current if item['id'] == created['id'])['position'] == 5


def test_parallel_category_updates_cannot_overwrite_each_other(client, catalog_rows, admin_headers):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: client.patch('/api/admin/categories/cat-filters', json={'name': name}, headers={**admin_headers, 'If-Match': '"1"'}), ['Первое имя', 'Второе имя']))
    assert sorted(response.status_code for response in results) == [200, 412]
    winner = next(response.json() for response in results if response.status_code == 200)
    current = client.get('/api/admin/categories', headers=admin_headers).json()['items']
    assert next(item for item in current if item['id'] == 'cat-filters')['name'] == winner['name']
    assert winner['version'] == 2


@pytest.mark.parametrize('body', [
    {'name': '   ', 'position': 0, 'isVisible': True, 'showInMenu': True},
    {'name': 'a' * 81, 'position': 0, 'isVisible': True, 'showInMenu': True},
    {'name': 'Bad', 'position': True, 'isVisible': True, 'showInMenu': True},
    {'name': 'Bad', 'position': 0, 'isVisible': 'false', 'showInMenu': True},
    {'name': 'Bad', 'position': -1, 'isVisible': True, 'showInMenu': True},
    {'name': 'Bad', 'position': 0, 'isVisible': True},
    {'name': 'Bad', 'position': 0, 'isVisible': True, 'showInMenu': True, 'id': 'client-id'},
    {'name': '\x00', 'position': 0, 'isVisible': True, 'showInMenu': True},
])
def test_category_create_validates_untrusted_fields(client, admin_headers, body):
    assert client.post('/api/admin/categories', json=body, headers=admin_headers).status_code == 422


@pytest.mark.parametrize('body', [{}, {'name': None}, {'isVisible': None}, {'position': None}, {'normalizedName': 'changed'}])
def test_category_patch_rejects_empty_null_and_internal_fields(client, catalog_rows, admin_headers, body):
    assert client.patch('/api/admin/categories/cat-filters', json=body, headers={**admin_headers, 'If-Match': '"1"'}).status_code == 422


@pytest.mark.parametrize('body', [{'websiteTitle': '\x00'}, {'websiteDescription': '\x00'}])
def test_presentation_rejects_text_postgres_cannot_store(client, catalog_rows, admin_headers, body):
    assert client.patch('/api/admin/products/p-visible/presentation', json=body, headers={**admin_headers, 'If-Match': '"1"'}).status_code == 422


def test_hidden_category_hides_products_and_restoring_does_not_activate_deleted_products(client, catalog_rows, admin_headers):
    response = client.patch('/api/admin/categories/cat-filters', json={'isVisible': False}, headers={**admin_headers, 'If-Match': '"1"'})
    assert response.status_code == 200
    assert client.get('/api/catalog/p-visible').status_code == 404
    response = client.patch('/api/admin/categories/cat-filters', json={'isVisible': True}, headers={**admin_headers, 'If-Match': '"2"'})
    assert response.status_code == 200
    assert client.get('/api/catalog/p-visible').status_code == 200
    assert client.get('/api/catalog/p-inactive').status_code == 404


def test_presentation_only_changes_website_fields(client, db, catalog_rows, admin_headers):
    from app.catalog import Product, ProductPresentation

    headers = {**admin_headers, 'If-Match': '"1"'}
    response = client.patch('/api/admin/products/p-visible/presentation', json={'categoryId': 'missing'}, headers=headers)
    assert response.status_code == 422
    for body in [{'priceKopecks': 1}, {'stock': 100}, {'sku': 'new'}, {}, {'websiteTitle': '  '}, {'imageIds': ['missing']}]:
        assert client.patch('/api/admin/products/p-visible/presentation', json=body, headers=headers).status_code == 422
    response = client.patch('/api/admin/products/p-visible/presentation', json={'categoryId': 'cat-oils', 'websiteTitle': '<b>Название сайта</b>', 'websiteDescription': 'Описание'}, headers=headers)
    assert response.status_code == 200
    assert response.json()['version'] == 2
    assert response.headers['etag'] == '"2"'
    assert client.patch('/api/admin/products/p-visible/presentation', json={'websiteTitle': 'Устаревшее'}, headers=headers).status_code == 412
    product = client.get('/api/catalog/p-visible').json()['product']
    assert product['name'] == 'Фильтр масляный'
    assert product['websiteTitle'] == '<b>Название сайта</b>'
    assert product['category'] == 'cat-oils'
    assert client.get('/api/catalog', params={'q': 'Название сайта'}).json()['total'] == 1
    db.expire_all()
    row = db.get(Product, 'p-visible')
    assert row.price_kopecks == 150000
    assert row.stock == 5
    row.name = 'Новое имя из 1С'
    db.commit()
    db.expire_all()
    assert db.get(ProductPresentation, 'p-visible').website_title == '<b>Название сайта</b>'
    assert client.get('/api/catalog', params={'q': 'Новое имя'}).json()['total'] == 1
    response = client.patch('/api/admin/products/p-visible/presentation', json={'websiteTitle': None, 'websiteDescription': None}, headers={**admin_headers, 'If-Match': '"2"'})
    assert response.status_code == 200
    assert response.json()['websiteTitle'] is None


def test_catalog_management_requires_server_admin_role(client, catalog_rows, manager_headers):
    assert client.get('/api/admin/categories', headers=manager_headers).status_code == 403
    assert client.get('/api/admin/products', headers=manager_headers).status_code == 403
    assert client.get('/api/admin/products/p-visible/presentation', headers=manager_headers).status_code == 403
    assert client.post('/api/admin/categories', json={'name': 'Forbidden', 'position': 0, 'isVisible': True, 'showInMenu': True}, headers=manager_headers).status_code == 403


def test_catalog_write_rejects_missing_csrf_and_version(client, catalog_rows, admin_headers):
    without_csrf = {key: value for key, value in admin_headers.items() if key.lower() != 'x-csrf-token'}
    assert client.post('/api/admin/categories', json={'name': 'Bad', 'position': 0, 'isVisible': True, 'showInMenu': True}, headers=without_csrf).status_code == 403
    assert client.patch('/api/admin/categories/cat-filters', json={'name': 'Bad'}, headers=admin_headers).status_code == 400
    assert client.patch('/api/admin/products/p-visible/presentation', json={'websiteTitle': 'Bad'}, headers=admin_headers).status_code == 400


def test_target_visibility_uses_same_product_and_category_rules(db, catalog_rows):
    from app.catalog import is_target_available, validate_target
    from app.common import ApiError

    assert is_target_available(db, {'type': 'catalog'})
    assert is_target_available(db, {'type': 'section', 'section': 'announcements'})
    assert is_target_available(db, {'type': 'product', 'productId': 'p-visible'})
    assert not is_target_available(db, {'type': 'product', 'productId': 'p-hidden'})
    assert not is_target_available(db, {'type': 'product', 'productId': 'p-inactive'})
    assert not is_target_available(db, {'type': 'category', 'categoryId': 'cat-hidden'})
    with pytest.raises(ApiError) as error:
        validate_target(db, {'type': 'product', 'productId': 'p-hidden'})
    assert error.value.status == 422
