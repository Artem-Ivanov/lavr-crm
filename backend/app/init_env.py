import secrets
import sys


def generate_env():
    values = {
        'APP_MODE': 'api', 'APP_BIND': '127.0.0.1', 'APP_PORT': '8080',
        'POSTGRES_DB': 'lavr', 'POSTGRES_USER': 'lavr',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'ADMIN_LOGIN': 'admin', 'ADMIN_PASSWORD': secrets.token_urlsafe(24),
        'SESSION_SECRET': secrets.token_urlsafe(48),
        'ALLOWED_ORIGINS': 'http://127.0.0.1:8080,http://localhost:8080',
        'COOKIE_SECURE': 'false', 'SEED_DEMO_CATALOG': 'true',
    }
    return '# Generated locally; keep this file private and outside image archives.\n' + ''.join(f'{key}={value}\n' for key, value in values.items())


if __name__ == '__main__':
    sys.stdout.write(generate_env())
