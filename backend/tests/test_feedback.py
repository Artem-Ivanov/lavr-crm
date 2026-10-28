from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import integration
from app.auth import AuditEntry, Staff, hash_password
from app.common import utcnow
from app.integration import Outbox
from app.orders import Order, OrderAttempt
from conftest import login_headers
from test_orders import order_body, order_product


def feedback_body(**changes):
    return {'type': 'contact', 'name': '', 'phone': '', 'email': 'buyer@example.test', 'messenger': {'service': '', 'address': ''}, 'message': '', **changes}


def submit(client, body=None, key=None, **headers):
    return client.post('/api/feedback', json=body or feedback_body(), headers={'Origin': 'http://testserver', 'Idempotency-Key': key or str(uuid4()), **headers})


def mutation(client, receipt, headers, suffix='status', body=None, version=1, key=None):
    args = {'json': body or {'status': 'in_progress'}, 'headers': {**headers, 'If-Match': f'"{version}"'}}
    if key:
        args['headers']['Idempotency-Key'] = key
    return getattr(client, 'patch' if suffix == 'status' else 'post')(f"/api/manager/feedback/{receipt['id']}/{suffix}", **args)


@pytest.mark.parametrize('contact', [
    {'email': '', 'phone': '8 (999) 123-45-67'},
    {'email': ' buyer@example.test '},
    {'email': '', 'messenger': {'service': ' Telegram ', 'address': ' @buyer '}},
])
def test_feedback_accepts_each_contact_without_erp_or_bot(client, contact, db):
    response = submit(client, feedback_body(**contact))
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert set(receipt) == {'id', 'number', 'createdAt', 'state'}
    assert receipt['state'] == 'accepted'
    event = db.get(Outbox, receipt['id'])
    assert event.event_type == 'feedback_telegram'
    assert event.payload == {'feedbackId': receipt['id']}
    assert event.state == 'queued'
    assert db.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize('changes', [
    {'email': ''}, {'type': 'request'}, {'phone': 'bad'}, {'phone': '+7٩٩٩١٢٣٤٥٦٧'},
    {'email': 'bad'}, {'email': 'a@example.test?subject=hello'}, {'name': 'name\nother'},
    {'email': 'a\r\n@example.test'}, {'message': 'text\x1b'}, {'message': 'x' * 2001},
    {'phone': '1' * 31}, {'name': 'n' * 101},
    {'messenger': {'service': 'Telegram', 'address': ''}},
    {'messenger': {'service': '', 'address': '@buyer'}},
    {'messenger': {'service': 'Telegram', 'address': ' \n '}},
    {'messenger': {'service': 'Telegram', 'address': '@b', 'private': True}},
    {'status': 'converted'}, {'assignedTo': 'admin'}, {'phone': None}, {'name': 42},
])
def test_feedback_rejects_every_invalid_supplied_contact(client, changes, db):
    response = submit(client, feedback_body(**changes))
    assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(Outbox)) == 0


def test_feedback_requires_origin_and_uuid_but_ignores_expired_session(client):
    body = feedback_body(type='request', message=' Нужен фильтр\nдля машины ')
    assert client.post('/api/feedback', json=body, headers={'Idempotency-Key': str(uuid4())}).status_code == 403
    assert submit(client, body, key='bad').status_code == 400
    assert submit(client, body, Cookie='lavr_session=expired').status_code == 202


def test_public_receipt_replays_immutable_after_private_mutation(client, manager_headers, db):
    key = str(uuid4())
    receipt = submit(client, feedback_body(email=' buyer@example.test '), key).json()
    case = mutation(client, receipt, manager_headers).json()
    assert case['assignedTo'] == {'id': 'manager', 'name': 'manager'}
    replay = submit(client, feedback_body(), key)
    assert replay.status_code == 202
    assert replay.json() == receipt
    assert submit(client, feedback_body(message='different'), key).status_code == 409
    assert db.scalar(select(func.count()).select_from(Outbox)) == 1


def test_private_list_detail_search_version_and_safe_notification(client, manager_headers):
    receipt = submit(client, feedback_body(phone='8 (999) 123-45-67', name='Иван', message='Вопрос')).json()
    path = f"/api/manager/feedback/{receipt['id']}"
    assert client.get(path, headers={'Cookie': ''}).status_code == 401
    assert client.get('/api/manager/feedback', headers={'Cookie': ''}).status_code == 401
    detail = client.get(path, headers=manager_headers)
    assert detail.status_code == 200, detail.text
    case = detail.json()
    assert detail.headers['etag'] == '"1"'
    assert detail.headers['cache-control'] == 'no-store'
    assert case['phone'] == '+79991234567'
    assert case['status'] == 'new' and case['order'] is None and case['version'] == 1
    assert case['notification'] == {'state': 'not_configured', 'code': None}
    listing = client.get('/api/manager/feedback', params={'q': 'buyer@example.test', 'status': 'new', 'offset': 0, 'limit': 1}, headers=manager_headers).json()
    assert listing['items'] == [case]
    assert listing['total'] == 1
    assert client.get('/api/manager/feedback', params={'q': '%'}, headers=manager_headers).json()['total'] == 0
    assert client.get('/api/manager/feedback/not-found', headers=manager_headers).status_code == 404
    assert client.get('/api/manager/feedback', params={'status': 'bad'}, headers=manager_headers).status_code == 422
    assert client.get(f"/api/feedback/{receipt['id']}").status_code == 404


def test_queue_keeps_new_cases_ahead_of_newer_claimed_cases(client, manager_headers):
    older_new = submit(client, feedback_body(email='older@example.test')).json()
    newer = submit(client, feedback_body(email='newer@example.test')).json()
    assert mutation(client, newer, manager_headers).status_code == 200
    items = client.get('/api/manager/feedback', headers=manager_headers).json()['items']
    assert [item['id'] for item in items] == [older_new['id'], newer['id']]


def test_claim_notes_close_enforce_owner_csrf_version_terminal_and_audit(client, manager_headers, admin_headers, db):
    receipt = submit(client).json()
    assert mutation(client, receipt, manager_headers, suffix='notes', body={'text': 'Позвонил'}).status_code == 409
    bad_headers = {**manager_headers, 'X-CSRF-Token': 'bad'}
    assert mutation(client, receipt, bad_headers).status_code == 403
    assert mutation(client, receipt, {**manager_headers, 'Origin': 'http://evil.test'}).status_code == 403
    claimed = mutation(client, receipt, manager_headers)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()['version'] == 2
    assert mutation(client, receipt, admin_headers).status_code == 412
    # A different manager may read but cannot mutate the owner's case.
    db.add(Staff(id='other', login='other', name='Other', role='manager', password_hash=hash_password('test-password-123'), is_active=True))
    db.commit()
    other = login_headers(client, 'other')
    assert mutation(client, receipt, other, version=1).status_code == 412
    assert mutation(client, receipt, other, suffix='notes', body={'text': 'Заметка'}, version=2).status_code == 403
    assert mutation(client, receipt, other, body={'status': 'closed', 'resolution': 'Закрыто'}, version=2).status_code == 403
    note = mutation(client, receipt, manager_headers, suffix='notes', body={'text': ' Ответил\nпо почте ', 'contactMethod': 'email'}, version=2)
    assert note.status_code == 200, note.text
    assert note.json()['version'] == 3
    assert note.json()['history'][-1]['text'] == 'Ответил\nпо почте'
    assert note.json()['history'][-1]['contactMethod'] == 'email'
    assert mutation(client, receipt, manager_headers, body={'status': 'closed', 'resolution': ' '}, version=3).status_code == 422
    closed = mutation(client, receipt, admin_headers, body={'status': 'closed', 'resolution': ' Вопрос решён '}, version=3)
    assert closed.status_code == 200, closed.text
    assert closed.json()['status'] == 'closed'
    assert closed.json()['resolution'] == 'Вопрос решён'
    assert closed.json()['history'][-1]['text'] == 'Вопрос решён'
    assert mutation(client, receipt, admin_headers, version=4).status_code == 409
    assert mutation(client, receipt, admin_headers, suffix='notes', body={'text': 'Поздно'}, version=4).status_code == 409
    assert mutation(client, receipt, admin_headers, body={'status': 'converted'}, version=4).status_code == 422
    actions = list(db.scalars(select(AuditEntry.action).where(AuditEntry.entity_id == receipt['id'])))
    assert 'feedback.claimed' in actions and 'feedback.closed' in actions


def test_unclaimed_case_can_be_closed_with_resolution(client, manager_headers):
    receipt = submit(client).json()
    closed = mutation(client, receipt, manager_headers, body={'status': 'closed', 'resolution': 'Дубль'})
    assert closed.status_code == 200
    assert closed.json()['assignedTo'] is None


def test_throttle_is_durable_each_contact_and_replay_is_free(client, db):
    key = str(uuid4())
    assert submit(client, key=key).status_code == 202
    for _ in range(3):
        assert submit(client, key=key).status_code == 202
    assert submit(client).status_code == 202
    assert submit(client).status_code == 202
    blocked = submit(client)
    assert blocked.status_code == 429, blocked.text
    assert 0 < int(blocked.headers['retry-after']) <= 600
    # A different contact remains available; exhausted contacts cannot hide behind another address.
    assert submit(client, feedback_body(email='other@example.test')).status_code == 202
    assert submit(client, feedback_body(phone='+79991234567')).status_code == 429


def test_throttle_ip_cannot_be_spoofed_by_forwarded_headers(client):
    for index in range(10):
        response = submit(client, feedback_body(email=f'buyer{index}@example.test'), **{'X-Forwarded-For': f'192.0.2.{index}', 'X-Real-IP': f'192.0.2.{index}'})
        assert response.status_code == 202, response.text
    assert submit(client, feedback_body(email='last@example.test')).status_code == 429


def test_claim_is_atomic_between_two_managers(client, manager_headers, admin_headers):
    receipt = submit(client).json()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda headers: mutation(client, receipt, headers), [manager_headers, admin_headers]))
    assert sorted(r.status_code for r in results) == [200, 412]


def test_conversion_is_atomic_replays_before_stale_version_and_disabled_erp(client, order_product, manager_headers, db, monkeypatch):
    key, public_key = str(uuid4()), str(uuid4())
    receipt = submit(client, key=public_key).json()
    assert mutation(client, receipt, manager_headers).status_code == 200
    body = order_body(order_product, 2)
    result = mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key)
    assert result.status_code == 201, result.text
    converted = result.json()
    assert converted['status'] == 'converted' and converted['version'] == 3
    assert converted['order']['totalKopecks'] == 25000
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert db.scalar(select(func.count()).select_from(OrderAttempt)) == 0
    assert db.scalar(select(func.count()).select_from(Outbox)) == 2
    db.refresh(order_product)
    assert order_product.reserved == 2
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    replay = mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key)
    assert replay.status_code == 200 and replay.json() == converted
    assert submit(client, key=public_key).json() == receipt
    changed = order_body(order_product, 1)
    assert mutation(client, receipt, manager_headers, suffix='order', body=changed, version=2, key=key).status_code == 409
    another = mutation(client, receipt, manager_headers, suffix='order', body=body, version=3, key=str(uuid4()))
    assert another.status_code == 409 and another.json()['code'] == 'FEEDBACK_ALREADY_CONVERTED'
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_conversion_failure_leaves_case_reserve_and_attempt_retriable(client, order_product, manager_headers, db, monkeypatch):
    receipt = submit(client).json()
    mutation(client, receipt, manager_headers)
    key, body = str(uuid4()), order_body(order_product)
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: integration.DisabledAdapter())
    result = mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key)
    assert result.status_code == 503, result.text
    assert result.json()['code'] == 'ERP_NOT_CONFIGURED'
    assert client.get(f"/api/manager/feedback/{receipt['id']}", headers=manager_headers).json()['status'] == 'in_progress'
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    db.refresh(order_product)
    assert order_product.reserved == 0
    monkeypatch.setattr(integration, 'get_erp_adapter', lambda: type('TestAdapter', (), {'is_configured': True})())
    assert mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key).status_code == 201


def test_conversion_competes_atomically_with_close_and_other_conversion(client, order_product, manager_headers, admin_headers, db):
    receipt = submit(client).json()
    mutation(client, receipt, manager_headers)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: mutation(client, receipt, manager_headers, suffix='order', body=order_body(order_product), version=2, key=str(uuid4())), range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    receipt = submit(client, feedback_body(email='second@example.test')).json()
    mutation(client, receipt, manager_headers)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(mutation, client, receipt, manager_headers, 'order', order_body(order_product), 2, str(uuid4())), workers.submit(mutation, client, receipt, admin_headers, 'status', {'status': 'closed', 'resolution': 'Отказ'}, 2)]
        results = [future.result() for future in futures]
    assert sorted(r.status_code for r in results) in ([201, 412], [200, 412])
    assert sum(r.status_code in (200, 201) for r in results) == 1


def test_public_idempotency_concurrent_attempts_create_one_receipt(client, db):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: submit(client, key=key), range(2)))
    assert all(response.status_code in (202, 409) for response in results)
    accepted = [response.json() for response in results if response.status_code == 202]
    assert accepted
    assert all(receipt == accepted[0] for receipt in accepted)
    assert submit(client, key=key).json() == accepted[0]
    assert db.scalar(select(func.count()).select_from(Outbox)) == 1


def test_contact_limits_apply_to_each_normalized_contact_without_plaintext_keys(client, db):
    from app.feedback import FeedbackThrottle
    body = feedback_body(phone='8 (999) 123-45-67', email=' Buyer@Example.test ', messenger={'service': 'Telegram', 'address': '@buyer'})
    for _ in range(3):
        assert submit(client, body).status_code == 202
    variants = [
        feedback_body(phone='+79991234567', email='other@example.test'),
        feedback_body(email='buyer@example.test'),
        feedback_body(email='', messenger={'service': 'telegram', 'address': '@BUYER'}),
    ]
    for variant in variants:
        assert submit(client, variant).status_code == 429
    counters = list(db.scalars(select(FeedbackThrottle)))
    assert len(counters) == 4
    assert all(len(counter.key) == 64 and counter.count == 3 for counter in counters)


def test_throttle_windows_expire_and_only_trusted_proxy_can_supply_client_ip(client, monkeypatch):
    from datetime import timedelta
    from app import feedback
    from app.config import settings
    monkeypatch.setattr(settings, 'trusted_proxy_hosts', ('testclient',), raising=False)
    first_ip = {'X-Real-IP': '192.0.2.1'}
    for index in range(10):
        assert submit(client, feedback_body(email=f'buyer{index}@example.test'), **first_ip).status_code == 202
    assert submit(client, feedback_body(email='blocked@example.test'), **first_ip).status_code == 429
    assert submit(client, feedback_body(email='other@example.test'), **{'X-Real-IP': '192.0.2.2'}).status_code == 202
    now = utcnow() + timedelta(minutes=11)
    monkeypatch.setattr(feedback, 'utcnow', lambda: now)
    assert submit(client, feedback_body(email='buyer0@example.test'), **first_ip).status_code == 202


def test_conversion_preserves_order_catalog_checks_and_requires_missing_contacts(client, order_product, manager_headers, db):
    receipt = submit(client).json()
    mutation(client, receipt, manager_headers)
    body = order_body(order_product)
    for customer in [{'name': '', 'phone': '+79991234567'}, {'name': 'Покупатель', 'phone': ''}]:
        invalid = {**body, 'customer': customer}
        assert mutation(client, receipt, manager_headers, suffix='order', body=invalid, version=2, key=str(uuid4())).status_code == 422
    body['items'][0]['expectedUnitPriceKopecks'] = 1
    response = mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=str(uuid4()))
    assert response.status_code == 409 and response.json()['code'] == 'PRICE_CHANGED'
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    db.refresh(order_product)
    assert order_product.reserved == 0


def test_backend_transaction_failure_rolls_back_order_case_reserve_and_conversion_key(client, order_product, manager_headers, db, db_engine):
    from app.feedback import Feedback
    receipt = submit(client).json()
    mutation(client, receipt, manager_headers)
    schema = db_engine.get_execution_options()['schema_translate_map'][None]
    # A real PostgreSQL constraint rejects the final outbox write, after the shared order helper flushed.
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f'ALTER TABLE {schema}.outbox ADD CONSTRAINT reject_order_export CHECK (event_type != \'order_export\')')
    key, body = str(uuid4()), order_body(order_product)
    response = mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key)
    assert response.status_code == 409, response.text
    db.expire_all()
    case = db.get(Feedback, receipt['id'])
    assert case.status == 'in_progress' and case.version == 2 and case.conversion_key is None and case.order_id is None
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.get(type(order_product), order_product.id).reserved == 0
    db.rollback()
    with db_engine.begin() as connection:
        connection.exec_driver_sql(f'ALTER TABLE {schema}.outbox DROP CONSTRAINT reject_order_export')
    assert mutation(client, receipt, manager_headers, suffix='order', body=body, version=2, key=key).status_code == 201


def test_notification_projection_preserves_sent_and_filters_unsafe_error_codes(client, manager_headers, db, monkeypatch):
    from app.config import settings
    for name in ('telegram_bot_token', 'telegram_chat_id', 'public_site_url'):
        monkeypatch.setattr(settings, name, 'configured', raising=False)
    receipt = submit(client).json()
    event = db.get(Outbox, receipt['id'])
    event.state, event.last_error_code = 'error', 'bad contact secret'
    db.commit()
    path = f"/api/manager/feedback/{receipt['id']}"
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'error', 'code': None}
    event.last_error_code = 'TELEGRAM_FORBIDDEN'
    db.commit()
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'error', 'code': 'TELEGRAM_FORBIDDEN'}
    event.state, event.last_error_code = 'done', None
    db.commit()
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    assert client.get(path, headers=manager_headers).json()['notification'] == {'state': 'done', 'code': None}
