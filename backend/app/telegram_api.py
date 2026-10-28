"""Bounded Bot API transport and server-side connection checks."""
import http.client
import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

from .config import settings

HTTP_TIMEOUT = 10
MAX_RESPONSE_BYTES = 65536


class DeliveryError(Exception):
    def __init__(self, code, retry_after=0, uncertain=False):
        self.code = code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code) else 'TELEGRAM_ERROR'
        self.retry_after = retry_after
        self.uncertain = uncertain
        super().__init__(self.code)


def site_origin():
    value = settings.public_site_url
    if not value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            return None
        parsed.port
    except ValueError:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))


def _call(method, payload):
    if method not in ('getMe', 'getChat', 'getChatMember', 'sendMessage'):
        raise DeliveryError('TELEGRAM_INVALID_METHOD')
    if not settings.telegram_bot_token:
        raise DeliveryError('TELEGRAM_NOT_CONFIGURED', retry_after=900)
    sending = method == 'sendMessage'
    connection = None
    try:
        connection = http.client.HTTPSConnection('api.telegram.org', timeout=HTTP_TIMEOUT)
        path = f'/bot{quote(settings.telegram_bot_token, safe=":")}/{method}'
        connection.request('POST', path, json.dumps(payload, ensure_ascii=False).encode(), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        try:
            data = json.loads(raw) if len(raw) <= MAX_RESPONSE_BYTES else None
        except (ValueError, UnicodeDecodeError, RecursionError):
            data = None
        error_code = data.get('error_code') if isinstance(data, dict) and data.get('ok') is False else None
        status = response.status
        if status == 429 or error_code == 429:
            parameters = data.get('parameters') if isinstance(data, dict) else None
            retry = parameters.get('retry_after') if isinstance(parameters, dict) else None
            raise DeliveryError('TELEGRAM_RATE_LIMIT', retry_after=min(retry, 604800) if type(retry) is int and retry > 0 else 60)
        rejected = error_code if type(error_code) is int else status
        if rejected in (400, 401, 403, 404):
            raise DeliveryError({401: 'TELEGRAM_AUTH_ERROR', 403: 'TELEGRAM_FORBIDDEN'}.get(rejected, 'TELEGRAM_REJECTED'), retry_after=900)
        if status >= 500 or (type(error_code) is int and error_code >= 500):
            raise DeliveryError('TELEGRAM_UNAVAILABLE', uncertain=sending)
        result = data.get('result') if isinstance(data, dict) else None
        if status != 200 or not isinstance(data, dict) or data.get('ok') is not True or not isinstance(result, dict):
            raise DeliveryError('TELEGRAM_INVALID_RESPONSE', uncertain=sending)
        return result
    except (OSError, http.client.HTTPException):
        # Bot API paths and transport exception text can contain the token.
        raise DeliveryError('TELEGRAM_NETWORK_ERROR', uncertain=sending) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except (OSError, http.client.HTTPException):
                pass


def send_telegram(payload):
    result = _call('sendMessage', payload)
    if type(result.get('message_id')) is not int or result['message_id'] <= 0:
        raise DeliveryError('TELEGRAM_INVALID_RESPONSE', uncertain=True)
    return result['message_id']


def check_connection():
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise DeliveryError('TELEGRAM_NOT_CONFIGURED', retry_after=900)
    if settings.public_site_url and not site_origin():
        raise DeliveryError('TELEGRAM_INVALID_SITE_URL', retry_after=900)
    bot = _call('getMe', {})
    bot_id = bot.get('id')
    if type(bot_id) is not int or bot_id <= 0 or bot.get('is_bot') is not True:
        raise DeliveryError('TELEGRAM_INVALID_BOT', retry_after=900)
    chat = _call('getChat', {'chat_id': settings.telegram_chat_id})
    if chat.get('type') != 'channel':
        raise DeliveryError('TELEGRAM_NOT_CHANNEL', retry_after=900)
    chat_id = chat.get('id')
    if type(chat_id) is not int or chat_id >= 0:
        raise DeliveryError('TELEGRAM_INVALID_RESPONSE')
    member = _call('getChatMember', {'chat_id': chat_id, 'user_id': bot_id})
    user = member.get('user')
    if not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] != bot_id or user.get('is_bot') is not True:
        raise DeliveryError('TELEGRAM_INVALID_BOT', retry_after=900)
    if member.get('status') != 'administrator' or member.get('can_post_messages') is not True:
        raise DeliveryError('TELEGRAM_NO_POST_PERMISSION', retry_after=900)
    return {'bot_id': bot_id, 'chat_id': chat_id}
