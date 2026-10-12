import logging
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from .common import ApiError
from .config import settings
from .db import get_db


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        limit = 6 * 1024 * 1024
        headers = dict(scope['headers'])
        length = headers.get(b'content-length')
        if length and (not length.isdigit() or len(length) > 10 or int(length) > limit):
            response = JSONResponse({'code': 'BODY_TOO_LARGE', 'message': 'Запрос слишком большой.', 'requestId': str(uuid4())}, status_code=413)
            return await response(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            event = await receive()
            if event['type'] == 'http.request':
                size += len(event.get('body', b''))
                if size > limit:
                    raise ApiError(413, 'BODY_TOO_LARGE', 'Запрос слишком большой.')
            return event

        return await self.app(scope, limited_receive, send)


def create_app():
    if len(settings.session_secret) < 32:
        raise RuntimeError('SESSION_SECRET должен содержать минимум 32 символа.')
    from . import auth, catalog, content, orders, integration
    app = FastAPI(title='LAVR CRM API', docs_url='/api/docs', openapi_url='/api/openapi.json')
    app.add_middleware(BodyLimitMiddleware)
    for module in (auth, catalog, content, orders, integration):
        app.include_router(module.router)

    @app.middleware('http')
    async def response_headers(request: Request, call_next):
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception as error:
            logging.getLogger('lavr.api').error('Request %s failed: %s', request.state.request_id, type(error).__name__)
            response = error_response(request, 500, 'INTERNAL_ERROR', 'Не удалось выполнить запрос. Повторите позже.')
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def error_response(request, status, code, message, details=None, headers=None):
        data = {'code': code, 'message': message, 'requestId': getattr(request.state, 'request_id', str(uuid4()))}
        if details is not None:
            data['details'] = details
        return JSONResponse(data, status_code=status, headers=headers or {})

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        return error_response(request, error.status, error.code, error.message, error.details, error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        fields = []
        for item in error.errors()[:50]:
            location = item['loc'][:-1] if item['type'] == 'extra_forbidden' else item['loc']
            fields.append('.'.join(str(part) for part in location)[:240])
        return error_response(request, 422, 'VALIDATION_ERROR', 'Проверьте формат и обязательные поля.', [{'field': field, 'code': 'INVALID_FIELD'} for field in fields])

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(request, error.status_code, 'HTTP_ERROR', 'Запрос не может быть выполнен.')

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, error):
        return error_response(request, 409, 'CONFLICT', 'Данные уже существуют или связанная запись изменилась.')

    @app.get('/healthz', include_in_schema=False)
    def health(db: Session = Depends(get_db)):
        db.execute(text('SELECT 1'))
        return {'status': 'ok'}

    return app
