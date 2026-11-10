"""Generate fresh, private development settings without reading application env."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

FAKE_TOKEN = '123456:development-only-token-000000000'
FILES = {'compose.env', 'relay.json', 'tls.crt', 'tls.key', 'ledger'}


def generate(runtime):
    runtime = Path(runtime).resolve()
    if runtime.exists():
        if all((runtime / name).exists() for name in FILES):
            return False
        raise FileExistsError('Partial runtime exists; move it aside explicitly before setup')
    runtime.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.notifier-setup-', dir=runtime.parent))
    try:
        staging.chmod(0o700)
        values = {
            'POSTGRES_USER': 'notifier_dev', 'POSTGRES_DB': 'mirzap67_notifier_test',
            'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
            'ADMIN_LOGIN': 'admin', 'ADMIN_PASSWORD': secrets.token_urlsafe(24),
            'SESSION_SECRET': secrets.token_urlsafe(48),
            'PII_ENCRYPTION_KEYS': json.dumps({'v1': secrets.token_urlsafe(32)}, separators=(',', ':')),
            'PII_ACTIVE_KEY_ID': 'v1', 'PII_INDEX_KEY': secrets.token_urlsafe(32),
            'CHALLENGE_SECRET': secrets.token_urlsafe(48),
            'NOTIFICATION_RELAY_TOKEN': secrets.token_urlsafe(32),
            'DEV_TELEGRAM_BOT_TOKEN': FAKE_TOKEN,
            'DEV_UID': str(os.getuid()), 'DEV_GID': str(os.getgid()),
        }
        config = {
            'sources': [{'id': 'mirzap67', 'token_sha256': hashlib.sha256(values['NOTIFICATION_RELAY_TOKEN'].encode()).hexdigest(), 'destinations': ['mirzap67.staff'], 'allowed_origins': ['https://crm.test']}],
            'aliases': {'mirzap67.staff': {'chat_id': '-1001234567890', 'revision': 'development-v1'}},
        }
        for name, text in (
            ('compose.env', '# Generated test credentials; do not commit.\n' + ''.join(f'{key}={value}\n' for key, value in values.items())),
            ('relay.json', json.dumps(config, indent=2) + '\n'),
        ):
            path = staging / name
            with open(path, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
                output.write(text)
        (staging / 'ledger').mkdir(mode=0o700)
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30',
            '-keyout', str(staging / 'tls.key'), '-out', str(staging / 'tls.crt'),
            '-subj', '/CN=notifier', '-addext', 'subjectAltName=DNS:notifier,DNS:localhost',
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        (staging / 'tls.key').chmod(0o600)
        (staging / 'tls.crt').chmod(0o644)
        staging.rename(runtime)
        return True
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, default=Path(__file__).resolve().parent / '.runtime')
    args = parser.parse_args()
    created = generate(args.runtime_dir)
    print(f'{"Created" if created else "Preserved existing"} development runtime: {args.runtime_dir}')


if __name__ == '__main__':
    main()
