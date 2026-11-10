"""Authenticated PII encryption; keys live outside the database."""
import base64
import binascii
import hashlib
import hmac
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


def _key(value):
    try:
        result = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError('PII key is not valid base64url') from error
    if len(result) != 32:
        raise ValueError('PII keys must contain 32 bytes')
    return result


def _aad(table, record_id, field):
    if not all(isinstance(value, str) and value for value in (table, record_id, field)):
        raise ValueError('PII encryption requires a stable record context')
    return json.dumps([1, table, record_id, field], separators=(',', ':')).encode()


def seal_json(value, *, table, record_id, field):
    key_id = settings.pii_active_key_id
    key = settings.pii_encryption_keys.get(key_id)
    if not key:
        raise ValueError('Active PII encryption key is unavailable')
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_key(key)).encrypt(nonce, json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), _aad(table, record_id, field))
    return {'format': 1, 'key_id': key_id, 'nonce': base64.urlsafe_b64encode(nonce).decode(), 'ciphertext': base64.urlsafe_b64encode(encrypted).decode()}


def open_json(envelope, *, table, record_id, field):
    try:
        if not isinstance(envelope, dict) or set(envelope) != {'format', 'key_id', 'nonce', 'ciphertext'} or type(envelope['format']) is not int or envelope['format'] != 1:
            raise ValueError('Invalid PII envelope')
        key = settings.pii_encryption_keys.get(envelope['key_id'])
        if not key:
            raise ValueError('PII decryption key is unavailable')
        nonce = base64.b64decode(envelope['nonce'], altchars=b'-_', validate=True)
        encrypted = base64.b64decode(envelope['ciphertext'], altchars=b'-_', validate=True)
        if len(nonce) != 12:
            raise ValueError('Invalid PII nonce')
        return json.loads(AESGCM(_key(key)).decrypt(nonce, encrypted, _aad(table, record_id, field)))
    except (InvalidTag, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as error:
        raise ValueError('Invalid PII ciphertext') from error


def seal_text(value, *, table, record_id, field):
    if not isinstance(value, str):
        raise ValueError('PII text must be a string')
    return '' if not value else json.dumps(seal_json(value, table=table, record_id=record_id, field=field), separators=(',', ':'))


def open_text(value, *, table, record_id, field):
    if value == '':
        return ''
    try:
        result = open_json(json.loads(value), table=table, record_id=record_id, field=field)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid PII ciphertext') from error
    if not isinstance(result, str):
        raise ValueError('Invalid PII text')
    return result


def lookup_digest(kind, normalized_value):
    return hmac.new(_key(settings.pii_index_key), json.dumps([kind, normalized_value], ensure_ascii=False, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()


def encrypted_property(field, *, text=False, nullable=False, id_field='id'):
    """Keep ORM callers on plaintext properties while binding storage to row IDs."""
    def context(record):
        from .common import new_id
        if not getattr(record, id_field, None):
            setattr(record, id_field, new_id())
        return dict(table=record.__tablename__, record_id=getattr(record, id_field), field=field)

    def get(record):
        value = getattr(record, '_' + field)
        if nullable and value is None:
            return None
        return (open_text if text else open_json)(value, **context(record))

    def set_value(record, value):
        encrypted = None if nullable and value is None else (seal_text if text else seal_json)(value, **context(record))
        setattr(record, '_' + field, encrypted)

    return property(get, set_value)


def validate_configuration():
    keys = settings.pii_encryption_keys
    if not isinstance(keys, dict) or not keys or settings.pii_active_key_id not in keys:
        raise ValueError('PII encryption keys and active key ID are required')
    decoded = []
    for key_id, value in keys.items():
        if not isinstance(key_id, str) or not key_id or not isinstance(value, str):
            raise ValueError('Invalid PII key map')
        decoded.append(_key(value))
    index = _key(settings.pii_index_key)
    if index in decoded:
        raise ValueError('PII index and encryption keys must be independent')
