"""Explicit fake transport injection, outside the production service package."""
import http.client

from internal_notifier import server, telegram_api


def send(payload):
    return telegram_api.send_telegram(
        payload,
        connection_factory=lambda: http.client.HTTPConnection('telegram-mock', 8080, timeout=10),
    )


if __name__ == '__main__':
    raise SystemExit(server.main(send=send))
