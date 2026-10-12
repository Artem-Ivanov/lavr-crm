import os
from pathlib import Path


class Settings:
    database_url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://lavr@127.0.0.1:5432/lavr')
    media_dir = Path(os.environ.get('MEDIA_DIR', '/data/media'))
    admin_login = os.environ.get('ADMIN_LOGIN', 'admin')
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    session_secret = os.environ.get('SESSION_SECRET', '')
    allowed_origins = tuple(value.strip() for value in os.environ.get('ALLOWED_ORIGINS', 'http://127.0.0.1:8080,http://localhost:8080').split(',') if value.strip())
    cookie_secure = os.environ.get('COOKIE_SECURE', 'true').lower() == 'true'
    seed_demo_catalog = os.environ.get('SEED_DEMO_CATALOG', 'false').lower() == 'true'
    session_hours = 8


settings = Settings()
