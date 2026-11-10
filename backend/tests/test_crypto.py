import base64

import pytest

from app import crypto
from app.config import settings


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, 'pii_encryption_keys', {'test': base64.urlsafe_b64encode(b'e' * 32).decode()}, raising=False)
    monkeypatch.setattr(settings, 'pii_active_key_id', 'test', raising=False)
    monkeypatch.setattr(settings, 'pii_index_key', base64.urlsafe_b64encode(b'i' * 32).decode(), raising=False)


def test_aead_roundtrip_random_nonce_and_no_plaintext():
    context = dict(table='orders', record_id='a', field='customer')
    value = {'email': 'private@example.test', 'phone': '+79991234567'}
    envelope = crypto.seal_json(value, **context)
    assert crypto.open_json(envelope, **context) == value
    assert envelope != crypto.seal_json(value, **context)
    assert 'private@example.test' not in str(envelope)


@pytest.mark.parametrize('context', [
    dict(table='feedback', record_id='a', field='customer'),
    dict(table='orders', record_id='b', field='customer'),
    dict(table='orders', record_id='a', field='fulfillment'),
])
def test_ciphertext_cannot_move_between_rows_tables_or_fields(context):
    envelope = crypto.seal_json({'secret': 'value'}, table='orders', record_id='a', field='customer')
    with pytest.raises(ValueError):
        crypto.open_json(envelope, **context)


def test_tamper_plaintext_and_missing_key_fail_closed(monkeypatch):
    context = dict(table='feedback', record_id='a', field='message')
    ciphertext = crypto.seal_text('private message', **context)
    assert crypto.open_text(ciphertext, **context) == 'private message'
    with pytest.raises(ValueError):
        crypto.open_text('legacy plaintext', **context)
    with pytest.raises(ValueError):
        crypto.open_json({'secret': 'plaintext'}, **context)
    envelope = crypto.seal_json({'secret': 'value'}, **context)
    envelope['ciphertext'] = 'AAAA'
    with pytest.raises(ValueError):
        crypto.open_json(envelope, **context)
    monkeypatch.setattr(settings, 'pii_encryption_keys', {})
    with pytest.raises(ValueError):
        crypto.open_text(ciphertext, **context)
    with pytest.raises(ValueError):
        crypto.seal_text('private message', **context)


def test_lookup_is_domain_separated_and_keyed(monkeypatch):
    first = crypto.lookup_digest('email', 'buyer@example.test')
    assert len(first) == 64
    assert first == crypto.lookup_digest('email', 'buyer@example.test')
    assert first != crypto.lookup_digest('phone', 'buyer@example.test')
    monkeypatch.setattr(settings, 'pii_index_key', base64.urlsafe_b64encode(b'j' * 32).decode())
    assert first != crypto.lookup_digest('email', 'buyer@example.test')


def test_startup_rejects_missing_malformed_or_reused_keys(monkeypatch):
    crypto.validate_configuration()
    monkeypatch.setattr(settings, 'pii_index_key', settings.pii_encryption_keys['test'])
    with pytest.raises(ValueError):
        crypto.validate_configuration()
    monkeypatch.setattr(settings, 'pii_index_key', 'short')
    with pytest.raises(ValueError):
        crypto.validate_configuration()
    monkeypatch.setattr(settings, 'pii_encryption_keys', {})
    with pytest.raises(ValueError):
        crypto.validate_configuration()
