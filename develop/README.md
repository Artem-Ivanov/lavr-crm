# Isolated notifier development

This five-service stack sends only to the local fake Bot API. It uses its own
PostgreSQL database (`mirzap67_notifier_test`), volumes, SQLite ledger, generated
credentials and self-signed gRPC TLS certificate. `https://crm.test` is a
test-only link origin; no website needs to exist there. No production `.env`
file is loaded. Registration, SMTP and demo catalog seeding are disabled.

Requirements: Python 3.13, OpenSSL, Docker Compose, and the independent
`internal-notifier` repository next to this CRM checkout. Image builds need
dependency access; running containers use an `internal: true` Docker network
with no Internet egress. The CRM SDK stays vendored in the backend image.

From the CRM root:

```sh
python3 develop/setup.py

# Use a clean child environment so existing application variables cannot
# override generated test settings. NOTIFIER_SOURCE is a build-time path only.
devcompose() {
  env -i PATH="$PATH" HOME="$HOME" \
    NOTIFIER_SOURCE="${NOTIFIER_SOURCE:-../../internal-notifier}" \
    docker compose --env-file develop/.runtime/compose.env \
      -f develop/compose.yaml "$@"
}

devcompose config --quiet
devcompose up --build -d --wait --wait-timeout 180
devcompose ps
devcompose down
```

To build a staged service elsewhere, set `NOTIFIER_SOURCE` to its absolute path
before calling `devcompose`, for example
`export NOTIFIER_SOURCE=/tmp/internal-notifier-20261030`.
The project name is `mirzap67-notifier-dev`. Do not combine this Compose file
with the root deployment files. `down` stops only this project and preserves
its volumes and ledger; `down --volumes` deliberately removes its PostgreSQL
and media volumes. The ledger remains in `develop/.runtime/ledger`.

The loopback endpoints are:

| Endpoint | Host address |
| --- | --- |
| Backend | `http://127.0.0.1:8083` |
| TLS gRPC | `localhost:50052` |
| PostgreSQL | `127.0.0.1:55442` |
| Fake Bot API/control | `http://127.0.0.1:8093` |

`develop/.runtime/compose.env` holds the generated admin password, source token,
database credentials and fresh PII keys. The directory is mode `0700`; secrets,
config and TLS private key are `0600`. The public certificate is `0644`. Setup
reruns preserve all existing files and ledger data, and refuse a partial
runtime. The certificate lasts 30 days. To create fresh test credentials,
explicitly remove the test volumes and runtime after stopping the stack;
this destroys its synthetic test data. Never replace keys against an existing
database you intend to retain.

The notifier runs as the generated host UID/GID so it can read private bind
mounts and write its dedicated ledger directory. The backend normally uses the
backend image's UID 1000. Run the smoke under the generated host identity to
read the private configuration:

```sh
devcompose exec -T --user "$(id -u):$(id -g)" backend python /develop/smoke.py
```

The smoke uses synthetic feedback and the real worker, TLS gRPC service,
durable ledger and HTTP parser. It can take a few seconds for sender retries.

The fake uses exactly `123456:development-only-token-000000000` and accepts only
`POST /bot<TOKEN>/sendMessage`, JSON with `chat_id` and nonempty `text`.
It never forwards a request. `GET /healthz` reports readiness.
`GET /messages` returns `{ "messages": [...] }`; each attempt records `payload`,
HTTP `status`, nullable `message_id`, and the scripted `response`.
Tokens are absent from captured records and request logs.

```sh
curl --fail http://127.0.0.1:8093/messages
curl --fail -H 'Content-Type: application/json' \
  -d '{"responses":[429,200]}' http://127.0.0.1:8093/control
```

`POST /control` replaces the next response sequence (at most 100 entries).
Supported entries are `200`, `400`, `401`, `403`, `404`, `429`, `500`, `502`,
`503`, `"malformed"` and `"disconnect"`. A `429` includes `retry_after: 1`;
the real sender's backoff still applies. Once the sequence is exhausted,
sends succeed with positive synthetic message IDs. `DELETE /messages` clears
captures, scripted responses and the message-ID counter; use it only when
you deliberately want to reset fake state. It does not reset the real ledger.
The serial HTTP server has five-second connection timeouts, a 64 KiB request
limit and at most 10,000 captured attempts.

Focused development checks, using an environment with pytest installed:

```sh
python3 -m pytest develop/tests -q
```

These tests exercise real loopback HTTP requests, private setup/rerun behavior,
TLS certificate names and rendered Compose isolation. They do not start or
stop containers. `notifier_mock_launcher.py` alone selects plain HTTP for this
stack; the independent production service keeps fixed Telegram HTTPS.
