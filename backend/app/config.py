import json
import os
from pathlib import Path


class Settings:
    database_url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://lavr@127.0.0.1:5432/lavr')
    media_dir = Path(os.environ.get('MEDIA_DIR', '/data/media'))
    admin_login = os.environ.get('ADMIN_LOGIN', '')
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    session_secret = os.environ.get('SESSION_SECRET', '')
    pii_encryption_keys = json.loads(os.environ.get('PII_ENCRYPTION_KEYS', '{}'))
    pii_active_key_id = os.environ.get('PII_ACTIVE_KEY_ID', 'v1')
    pii_index_key = os.environ.get('PII_INDEX_KEY', '')
    challenge_secret = os.environ.get('CHALLENGE_SECRET', '')
    customer_registration_enabled = os.environ.get('CUSTOMER_REGISTRATION_ENABLED', 'false').lower() == 'true'
    smtp_host = os.environ.get('SMTP_HOST', '')
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_username = os.environ.get('SMTP_USERNAME', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    smtp_from = os.environ.get('SMTP_FROM', '')
    smtp_tls_mode = os.environ.get('SMTP_TLS_MODE', 'tls')
    allowed_origins = tuple(value.strip() for value in os.environ.get('ALLOWED_ORIGINS', 'http://127.0.0.1:8080,http://localhost:8080').split(',') if value.strip())
    cookie_secure = os.environ.get('COOKIE_SECURE', 'true').lower() == 'true'
    seed_demo_catalog = os.environ.get('SEED_DEMO_CATALOG', 'false').lower() == 'true'
    telegram_bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_notifications_enabled = os.environ.get('TELEGRAM_NOTIFICATIONS_ENABLED', 'false').lower() == 'true'
    notification_transport = os.environ.get('NOTIFICATION_TRANSPORT', 'direct')
    notification_relay_target = os.environ.get('NOTIFICATION_RELAY_TARGET', '')
    notification_relay_token = os.environ.get('NOTIFICATION_RELAY_TOKEN', '')
    notification_destination_alias = os.environ.get('NOTIFICATION_DESTINATION_ALIAS', 'mirzap67.staff')
    notification_relay_ca_file = os.environ.get('NOTIFICATION_RELAY_CA_FILE', '')
    public_site_url = os.environ.get('PUBLIC_SITE_URL', '')
    trusted_proxy_hosts = tuple(value.strip() for value in os.environ.get('TRUSTED_PROXY_HOSTS', '').split(',') if value.strip())
    session_hours = 8


settings = Settings()


def validate_notification_transport(transport: str | None = None) -> str:
    from .telegram_api import DeliveryError

    value = settings.notification_transport if transport is None else transport
    if value not in ('direct', 'relay'):
        raise DeliveryError('NOTIFICATION_TRANSPORT_INVALID')
    return value
