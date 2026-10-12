import re
from datetime import datetime, timezone
from uuid import uuid4


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None, headers=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid4())


def version_header(value):
    if not isinstance(value, str) or len(value) > 18 or not re.fullmatch(r'"[1-9][0-9]*"', value):
        raise ApiError(400, 'INVALID_VERSION', 'Нужен корректный заголовок If-Match.')
    number = int(value[1:-1])
    if number > 9007199254740991:
        raise ApiError(400, 'INVALID_VERSION', 'Некорректная версия записи.')
    return number


def check_version(row, value):
    if row.version != version_header(value):
        raise ApiError(412, 'VERSION_CONFLICT', 'Запись уже изменена. Загрузите актуальную версию.')


def page_response(items, total, offset, limit, **extra):
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit, **extra}
