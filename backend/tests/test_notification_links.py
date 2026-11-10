import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.common import utcnow
from app.config import settings
from app.telegram_api import DeliveryError


@pytest.fixture(autouse=True)
def trusted_origin(monkeypatch):
    monkeypatch.setattr(settings, 'public_site_url', 'https://shop.example/')


def test_site_links_match_shared_browser_contract():
    from app.notification_links import site_link

    fixture = Path(__file__).resolve().parents[2] / 'tests/fixtures/navigation-v1.json'
    for case in json.loads(fixture.read_text())['valid']:
        assert site_link(case['page'], case['query']) == 'https://shop.example/' + case['hash']


def test_feedback_payload_preserves_legacy_url_and_safe_text():
    from app.notification_worker import notification_payload

    feedback = SimpleNamespace(id='e7435b12-3bf9-4d32-80cb-8f7048f5c610', number=12,
                               type='request', created_at=datetime(2026, 11, 2, 10, 45, tzinfo=timezone.utc), message='private', email='private@example.test')
    payload = notification_payload(feedback)
    assert payload['reply_markup']['inline_keyboard'][0][0]['url'] == (
        'https://shop.example/#crm-feedback?case=e7435b12-3bf9-4d32-80cb-8f7048f5c610')
    assert 'private' not in payload['text']
    assert payload['text'] == 'Новое обращение №12 от 02-11-2026 13:45\nОставить запрос'


def test_site_link_orders_keys_and_encodes_opaque_ids_once():
    from app.notification_links import site_link

    assert site_link('catalog', {'product': 'a%20~*', 'category': 'oils'}) == (
        'https://shop.example/#catalog?category=oils&product=a%2520%7E*')
    identifier = '😀' * 128
    url = site_link('crm', {'order': identifier})
    assert parse_qsl(urlsplit(url).fragment.split('?', 1)[1]) == [('order', identifier)]


@pytest.mark.parametrize('page', ['home', 'catalog', 'cart', 'crm', 'crm-feedback', 'account',
                                  'account-orders', 'account-feedback', 'admin-users', 'crm-catalog', 'crm-content'])
def test_default_pages_have_no_resource_parameters(page):
    from app.notification_links import site_link

    assert site_link(page) == f'https://shop.example/#{page}'


@pytest.mark.parametrize(('page', 'parameters'), [
    ('https://evil.example', None), ('login', None), ('signup', None), ('unknown', None),
    ('crm', {'returnTo': '#crm'}), ('crm', {'order': 'x', 'token': 'secret'}),
    ('crm', [('order', 'a'), ('order', 'b')]), ('crm', {'order': ['a', 'b']}),
    ('crm', {'order': ''}), ('crm', {'order': '.'}), ('crm', {'order': '..'}),
    ('crm', {'order': '😀' * 129}), ('crm', {'order': '\x00x'}), ('crm', {'order': '\x85x'}),
    ('crm', {'order': '\ud800'}), ('home', {'section': 'invalid'}),
    ('home', {'section': 'offers', 'publication': 'x'}), ('crm-content', {'tab': 'invalid'}),
    ('crm-content', {'publication': 'x'}), ('crm-content', {'tab': 'media', 'publication': 'x'}),
    ('cart', {'order': 'x'}),
])
def test_site_link_rejects_unsafe_or_unsupported_parameters(page, parameters):
    from app.notification_links import site_link

    with pytest.raises(DeliveryError, match='^NOTIFICATION_INVALID_LINK$'):
        site_link(page, parameters)


@pytest.mark.parametrize('origin', ['', 'https://user:pass@shop.example', 'https://shop.example/path',
                                    'https://shop.example?token=secret', 'https://shop.example/#crm',
                                    '//evil.example', 'https://shop.example\n', 'https://shop.example:invalid'])
def test_site_link_uses_only_trusted_valid_origin(monkeypatch, origin):
    from app.notification_links import site_link

    monkeypatch.setattr(settings, 'public_site_url', origin)
    with pytest.raises(DeliveryError, match='^TELEGRAM_NOT_CONFIGURED$'):
        site_link('crm')


def test_site_link_omits_default_cms_tab():
    from app.notification_links import site_link

    assert site_link('crm-content', {'tab': 'site'}) == 'https://shop.example/#crm-content'
