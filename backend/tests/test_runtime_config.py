import pytest


def test_startup_refuses_missing_challenge_secret(monkeypatch):
    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, 'challenge_secret', '')
    with pytest.raises(RuntimeError, match='CHALLENGE_SECRET'):
        create_app()


@pytest.mark.parametrize('path', ['/api/docs', '/api/openapi.json'])
def test_system_api_documentation_requires_operational_access(client, partner_headers, manager_headers, admin_headers, path):
    assert client.get(path, headers={'Cookie': ''}).status_code == 401
    assert client.get(path, headers=partner_headers).status_code == 403
    assert client.get(path, headers=manager_headers).status_code == 200
    assert client.get(path, headers=admin_headers).status_code == 200


@pytest.mark.parametrize('transport', ['unknown', '', 'RELAY'])
def test_startup_refuses_unknown_notification_transport(monkeypatch, transport):
    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, 'notification_transport', transport)
    with pytest.raises(RuntimeError, match='NOTIFICATION_TRANSPORT'):
        create_app()
