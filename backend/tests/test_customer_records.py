import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import crypto
from app.common import utcnow
from app.feedback import Feedback, FeedbackNote
from app.integration import Outbox
from app.orders import Order, OrderAttempt
from test_orders import order_body, order_product, submit as guest_order
from test_feedback import feedback_body, submit as guest_feedback, mutation


@pytest.fixture
def customer(db, client):
    from app.auth import User, hash_password
    user = User(id='customer-a', name='', role='partner', is_active=True,
                password_hash=hash_password('customer-password-123'), email_verified_at=utcnow())
    user.profile = {'email': 'private-buyer@example.test', 'phone': '+79991112233'}
    user.email_lookup = crypto.lookup_digest('email', 'private-buyer@example.test')
    db.add(user)
    db.commit()
    response = client.post('/api/customer/session', json={'email': 'private-buyer@example.test', 'password': 'customer-password-123'}, headers={'Origin': 'http://testserver', 'Cookie': ''})
    assert response.status_code == 200, response.text
    return user, {'Cookie': 'lavr_session=' + response.cookies['lavr_session'], 'Origin': 'http://testserver', 'X-CSRF-Token': response.json()['csrfToken']}


def own_order(client, product, headers, *, key=None, **changes):
    body = order_body(product)
    body.pop('customer')
    body.update(changes)
    return client.post('/api/customer/orders', json=body, headers={**headers, 'Idempotency-Key': key or str(uuid4())})


def own_feedback(client, headers, *, key=None, **changes):
    return client.post('/api/customer/feedback', json={'type': 'request', 'message': 'Private request', **changes}, headers={**headers, 'Idempotency-Key': key or str(uuid4())})


def test_all_stored_pii_copies_are_encrypted(client, db, order_product, manager_headers):
    receipt = guest_order(client, order_product).json()
    body = order_body(order_product)
    body['fulfillment'] = {'method': 'delivery', 'address': 'Private delivery address'}
    case = guest_feedback(client, feedback_body(name='Private name', message='Private request')).json()
    assert mutation(client, case, manager_headers).status_code == 200
    assert mutation(client, case, manager_headers, 'notes', {'text': 'Private staff note'}, version=2).status_code == 200
    conversion = mutation(client, case, manager_headers, 'order', body, version=3, key=str(uuid4()))
    assert conversion.status_code == 201, conversion.text
    raw = []
    for model in (Order, Feedback, FeedbackNote, Outbox):
        raw.extend(dict(row) for row in db.execute(select(model.__table__)).mappings())
    stored = json.dumps(raw, ensure_ascii=False, default=str)
    for value in ('Private delivery address', 'Private name', 'Private request', 'Private staff note', 'buyer@example.test', '+79991234567'):
        assert value not in stored
    assert db.get(Order, receipt['id']).fulfillment == {'method': 'pickup', 'address': ''}
    assert db.get(Order, conversion.json()['order']['id']).fulfillment['address'] == 'Private delivery address'
    assert db.get(Feedback, case['id']).conversion_response['history'][-1]['action'] == 'feedback.converted'


def test_customer_records_are_owned_profile_bound_and_private(client, db, customer, order_product, manager_headers):
    user, headers = customer
    guest = guest_order(client, order_product).json()
    response = own_order(client, order_product, headers)
    assert response.status_code == 201, response.text
    order = response.json()
    assert set(order) == {'id', 'number', 'createdAt', 'status', 'totalKopecks', 'items', 'fulfillment'}
    assert db.get(Order, order['id']).customer == {'name': '', 'phone': '+79991112233', 'email': 'private-buyer@example.test'}
    assert db.get(Order, order['id']).customer_id == user.id
    listing = client.get('/api/customer/orders', headers=headers).json()
    assert listing['total'] == 1 and listing['items'] == [order]
    assert client.get('/api/customer/orders/' + guest['id'], headers=headers).status_code == 404
    case_response = own_feedback(client, headers)
    assert case_response.status_code == 202, case_response.text
    case = case_response.json()
    assert set(case) == {'id', 'number', 'createdAt', 'type', 'message', 'status', 'order'}
    assert case['order'] is None
    assert client.get('/api/customer/feedback', headers=headers).json()['items'] == [case]
    assert client.get('/api/customer/feedback/' + case['id'], headers={'Cookie': ''}).status_code == 401
    assert own_order(client, order_product, headers, customer={'phone': '+79990000000'}).status_code == 422
    assert own_feedback(client, headers, customerId='other').status_code == 422
    assert own_feedback(client, {**headers, 'X-CSRF-Token': 'bad'}).status_code == 403
    raw = json.dumps(dict(db.execute(select(user.__table__).where(user.__table__.c.id == user.id)).mappings().one()), default=str)
    assert 'private-buyer@example.test' not in raw and '+79991112233' not in raw


def test_attempts_bind_actor_and_replay_never_discloses_other_owner(client, db, customer, order_product):
    user, headers = customer
    key = str(uuid4())
    initial = own_order(client, order_product, headers, key=key)
    assert initial.status_code == 201, initial.text
    assert own_order(client, order_product, headers, key=key).json() == initial.json()
    assert guest_order(client, order_product, key=key).status_code == 409
    feedback_key = str(uuid4())
    first_case = own_feedback(client, headers, key=feedback_key)
    assert first_case.status_code == 202
    assert guest_feedback(client, key=feedback_key).status_code == 409
    from app.auth import User
    other = User(id='customer-b', name='', role='partner', is_active=True, password_hash=user.password_hash, email_verified_at=utcnow())
    other.profile = {'email': 'other@example.test', 'phone': '+79994445566'}
    other.email_lookup = crypto.lookup_digest('email', 'other@example.test')
    db.add(other)
    db.commit()
    login = client.post('/api/customer/session', json={'email': 'other@example.test', 'password': 'customer-password-123'}, headers={'Origin': 'http://testserver', 'Cookie': ''})
    other_headers = {'Cookie': 'lavr_session=' + login.cookies['lavr_session'], 'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrfToken']}
    assert own_order(client, order_product, other_headers, key=key).status_code == 409
    assert own_feedback(client, other_headers, key=feedback_key).status_code == 409
    assert client.get('/api/customer/orders/' + initial.json()['id'], headers=other_headers).status_code == 404
    assert client.get('/api/customer/feedback/' + first_case.json()['id'], headers=other_headers).status_code == 404


def test_guest_cookie_guard_profile_requirement_and_no_contact_auto_claim(client, db, customer, order_product, manager_headers):
    user, headers = customer
    body = feedback_body(email='private-buyer@example.test', phone='+79991112233')
    guest = guest_feedback(client, body).json()
    assert db.get(Feedback, guest['id']).customer_id is None
    for path, body in [('/api/orders', order_body(order_product)), ('/api/feedback', feedback_body())]:
        for cookie, status in [(headers['Cookie'], 409), ('lavr_session=expired', 401), (manager_headers['Cookie'], 409)]:
            result = client.post(path, json=body, headers={'Origin': 'http://testserver', 'Cookie': cookie, 'Idempotency-Key': str(uuid4())})
            assert result.status_code == status, result.text
    assert client.get('/api/customer/orders', headers=manager_headers).status_code == 409
    assert client.get('/api/customer/feedback/' + guest['id'], headers=headers).status_code == 404


def test_customer_conversion_propagates_owner_and_hides_internal_fields(client, db, customer, order_product, manager_headers):
    user, headers = customer
    case = own_feedback(client, headers).json()
    assert mutation(client, case, manager_headers).status_code == 200
    body = order_body(order_product)
    result = mutation(client, case, manager_headers, 'order', body, version=2, key=str(uuid4()))
    assert result.status_code == 201, result.text
    order_id = result.json()['order']['id']
    assert db.get(Order, order_id).customer_id == user.id
    visible = client.get('/api/customer/feedback/' + case['id'], headers=headers).json()
    assert set(visible['order']) == {'id', 'number', 'createdAt', 'status', 'totalKopecks'}
    assert visible['order']['id'] == order_id


def test_legacy_guest_v1_retry_preserves_original_digest_and_receipt(client, db, order_product):
    import hashlib
    from app.feedback import CreateFeedbackRequest
    from app.orders import CreateOrderRequest
    key = str(uuid4())
    body = order_body(order_product)
    receipt = guest_order(client, order_product, key=key).json()
    attempt = db.get(OrderAttempt, key)
    attempt.digest_version = 1
    attempt.digest = hashlib.sha256(json.dumps(CreateOrderRequest(**body).model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    case_key = str(uuid4())
    case_receipt = guest_feedback(client, key=case_key).json()
    case = db.get(Feedback, case_receipt['id'])
    case.digest_version = 1
    case.digest = hashlib.sha256(json.dumps(CreateFeedbackRequest(**feedback_body()).model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    db.commit()
    assert guest_order(client, order_product, key=key).json() == receipt
    assert guest_feedback(client, key=case_key).json() == case_receipt


def test_staff_search_accepts_only_full_normalized_contacts(client, db, order_product, manager_headers):
    email = 'buyer-' + 'x' * 115 + '@example.test'
    body = order_body(order_product)
    body['customer']['email'] = email.upper()
    guest_order(client, order_product, body=body)
    guest_feedback(client, feedback_body(email=email.upper(), phone='+79991234567', name='Unsearchable buyer'))
    for path in ('orders', 'feedback'):
        for value in (email, '+7 (999) 123-45-67'):
            result = client.get('/api/manager/' + path, params={'q': value}, headers=manager_headers)
            assert result.status_code == 200, result.text
            assert result.json()['total'] == 1
        for value in ('buyer', '999123', 'Покупатель', 'Unsearchable'):
            assert client.get('/api/manager/' + path, params={'q': value}, headers=manager_headers).json()['total'] == 0


def test_legacy_delivery_customer_receipt_encrypts_address_and_stays_immutable(client, db, customer, order_product):
    from app import orders
    from app.customer_records import customer_order

    user, headers = customer
    key = str(uuid4())
    body = orders.CreateOrderRequest.model_construct(
        customer=orders.CustomerInput.model_construct(name='', phone=user.profile['phone'], email=user.profile['email']),
        fulfillment=orders.FulfillmentInput(method='delivery', address='Private receipt street'),
        items=[orders.OrderItemInput(**order_body(order_product)['items'][0])],
    )
    order = orders.build_order(db, body, customer_id=user.id)
    initial = customer_order(order)
    db.add(OrderAttempt(idempotency_key=key, actor=user.id, digest_version=2,
                        digest=orders.request_digest(body, user.id), order_id=order.id, receipt=initial))
    db.commit()
    raw = db.execute(select(OrderAttempt.__table__.c.receipt).where(OrderAttempt.__table__.c.idempotency_key == key)).scalar_one()
    assert 'Private receipt street' not in json.dumps(raw)
    order.status = 'confirmed'
    db.commit()
    replay = own_order(client, order_product, headers, key=key, fulfillment={'method': 'delivery', 'address': 'Private receipt street'})
    assert replay.status_code == 200
    assert replay.json() == initial


@pytest.mark.parametrize(('legacy_name', 'role'), [('L' * 101, 'operator'), ('L' * 240, 'admin'), ('Legacy\nOperator', 'operator')], ids=('operator-101', 'admin-240', 'operator-multiline'))
def test_legacy_display_name_is_preserved_without_becoming_buyer_name(client, db, customer, order_product, legacy_name, role):
    user, headers = customer
    user.login = 'legacy-account'
    user.name = legacy_name
    user.role = role
    db.commit()
    response = own_feedback(client, headers)
    assert response.status_code == 202, response.text
    case = db.get(Feedback, response.json()['id'])
    assert case.customer_id == user.id and case.name == ''
    assert case.phone == '+79991112233' and case.email == 'private-buyer@example.test'
    response = own_order(client, order_product, headers)
    assert response.status_code == 201, response.text
    assert db.get(Order, response.json()['id']).customer['name'] == ''
    response = own_order(client, order_product, headers, name='Buyer name')
    assert response.status_code == 201, response.text
    assert db.get(Order, response.json()['id']).customer['name'] == 'Buyer name'
    db.refresh(user)
    assert user.name == legacy_name


def test_historical_customer_digest_replays_original_unitless_receipt(client, db, customer, order_product):
    import hashlib
    from sqlalchemy import func

    user, headers = customer
    key = str(uuid4())
    first = own_order(client, order_product, headers, key=key)
    assert first.status_code == 201, first.text
    original = first.json()
    original['items'] = [{key: value for key, value in item.items() if key not in {'unit', 'lineTotalKopecks'}} for item in original['items']]
    frozen = {'customer': {'name': '', 'phone': '+79991112233', 'email': 'private-buyer@example.test'}, 'fulfillment': {'method': 'pickup', 'address': ''}, 'items': [{'productId': order_product.id, 'quantity': 1, 'expectedUnitPriceKopecks': 12500}]}
    attempt = db.get(OrderAttempt, key)
    attempt.receipt = original
    attempt.digest = hashlib.sha256(json.dumps({'actor': user.id, 'body': frozen}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    db.commit()
    replay = own_order(client, order_product, headers, key=key)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_fractional_customer_projection_uses_stored_unit_and_line_amount(client, db, customer, order_product):
    _, headers = customer
    order_product.unit = 'л'
    order_product.price_kopecks = 101
    db.commit()
    items = [{'productId': order_product.id, 'quantity': 0.5, 'expectedUnitPriceKopecks': 101, 'expectedUnit': 'л'}]
    response = own_order(client, order_product, headers, items=items)
    assert response.status_code == 201, response.text
    assert response.json()['totalKopecks'] == 51
    item = response.json()['items'][0]
    assert (item['quantity'], item['unit'], item['lineTotalKopecks']) == (0.5, 'л', 51)
