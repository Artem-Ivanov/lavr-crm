import json
import secrets
import sys


def generate_env():
    values = {
        'APP_MODE': 'api', 'APP_BIND': '127.0.0.1', 'APP_PORT': '8080',
        'POSTGRES_DB': 'lavr', 'POSTGRES_USER': 'lavr',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'ADMIN_LOGIN': '', 'ADMIN_PASSWORD': secrets.token_urlsafe(24),
        'SESSION_SECRET': secrets.token_urlsafe(48),
        'PII_ENCRYPTION_KEYS': json.dumps({'v1': secrets.token_urlsafe(32)}, separators=(',', ':')),
        'PII_ACTIVE_KEY_ID': 'v1', 'PII_INDEX_KEY': secrets.token_urlsafe(32),
        'CHALLENGE_SECRET': secrets.token_urlsafe(48),
        'CUSTOMER_REGISTRATION_ENABLED': 'false',
        'SMTP_HOST': '', 'SMTP_PORT': '465', 'SMTP_TLS_MODE': 'tls',
        'SMTP_USERNAME': '', 'SMTP_PASSWORD': '', 'SMTP_FROM': '',
        'ALLOWED_ORIGINS': 'http://127.0.0.1:8080,http://localhost:8080',
        'COOKIE_SECURE': 'false', 'SEED_DEMO_CATALOG': 'true',
        'TELEGRAM_NOTIFICATIONS_ENABLED': 'false',
        'NOTIFICATION_TRANSPORT': 'direct', 'NOTIFICATION_RELAY_TARGET': '',
        'NOTIFICATION_RELAY_TOKEN': '', 'NOTIFICATION_RELAY_CA_FILE': '',
        'NOTIFICATION_DESTINATION_ALIAS': 'mirzap67.staff',
        'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': '', 'PUBLIC_SITE_URL': '',
    }
    return '# Generated locally; keep this file private and outside image archives.\n' + ''.join(f'{key}={value}\n' for key, value in values.items())


if __name__ == '__main__':
    sys.stdout.write(generate_env())
