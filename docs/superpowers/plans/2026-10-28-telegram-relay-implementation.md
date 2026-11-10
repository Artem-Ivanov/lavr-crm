# MIRZAP67 Telegram Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a standalone authenticated gRPC-to-Telegram relay and a website outbox integration that distinguishes durable acceptance from actual Telegram confirmation.

**Architecture:** Reuse the existing Python backend runtime image and vetted telegram_api transport, with an independent `python -m app.telegram_relay` process, SQLite durable ledger and one sender. Website worker submits immutable source snapshots and polls receipts. No Redis/broker or public access to CRM PostgreSQL.

**Tech Stack:** Python3.13, sqlite3, grpcio==1.85.0,protobuf==7.36.2; grpcio-tools==1.85.0 development generation only; existing Telegram HTTPS helper.

**Spec:** ../specs/2026-10-28-telegram-relay-design.md. Root clarifications here are explicit implementation rulings, not changes to user account permissions.

## Global Constraints

- No production infrastructure/env/DB changes, live messages, paid calls or secrets printed. Actual VM Telegram egress remains unavailable; a separate reachable host is required for production.
- gRPC endpoint TLS required; per-source high-entropy credential in authorization metadata, server stores SHA256 verifier; source identity derived from credentials. Alias ACL and allowed HTTPS origins enforced.
- Alias mirzap67.staff maps to configured -1003744016989; bot token/chat config never accepted in Submit. V1 one configured bot per instance; multiple source identities and destination aliases allowed.
- Canonical proto is the full notifications.v1 schema from spec: SubmitNotification/GetNotification, immutable UUID request_id + request_created_at, text+optionalButton; NotificationReceipt with safe state/times/message_id/duplicate_possible.
- Durable SQLite WAL/synchronous FULL; unique(source,request_id)+canonical payload digest, same payload returns same receipt, conflict ALREADY_EXISTS; persist destination revision at acceptance. No network in DB transaction.
- Limits32KiB RPC, text1..4096 Unicode characters with safe Bot API length check, label1..64,url<=2048bytes, alias<=80ASCII; no rawparse_mode, previewsdisabled, no credentials inURLs. Source-specific allowed origins; mirzap67 links match protected CRM case route.
- Command creation window7days, future skew5minutes; existing keys return retained receipt, unknown expired commands fail; delivery expires7days; completed payload30days then tombstone30days. No automatic new request IDs after uncertainty/expiry.
- Sender lease60s,fencing,Telegramtimeout10s, retry5/30/120/900s+jitter; global bot429 pause and oneattempt/second; restart after leased send treated UNCERTAIN. Only TELEGRAM_ACCEPTED with positive message_id means sent; duplicate_possible survives uncertainty.
- Ordinary website HTTP notification states remain queued/running/done/error/uncertain/not_configured; accepted relay != done. Caller outbox remains transactional with feedback.

## Review Focus

- Lost Submit reply after commit must not double-create/send; durable same-key replay across restart.
- Timeout after Telegram accepted and before ledger commit must persist uncertainty rather than false failure/exactly-once claims.
- Source A cannot query source B or send through unauthorized aliases/origins; no credentials in errors/logs.
- Missing/changed relay config after source acceptance cannot fall back to direct Telegram and duplicate delivery.
- Website done must require persisted actual Telegram message ID, not merely a successful gRPC call.

## Implementation Decisions and Interfaces

Root owns runtime config/requirements/Compose/docs; worker owns new relay/protocol/client modules and notification_worker.py plus focused feedback notification projection changes/tests. No account/auth/crypto/registration/UI files are edited.

- `backend/app/notification_protocol/notifications.proto` canonical contract; generated Python siblings via `python -m grpc_tools.protoc -I backend --python_out=backend --grpc_python_out=backend .../notifications.proto`. Keep generated imports valid under app.notification_protocol; no manual runtime substitutes.
- `notification_ledger.py` SQLite storage/claim/result/cleanup; `telegram_relay.py` grpc auth/validation/service+single sender/process startup. Reuse `telegram_api.send_telegram` without importing CRM auth/database modules.
- `notification_relay_client.py` TLS gRPC source client with bounded Submit5s/Get3s deadlines. Constructor reads root settings; metadata token, default system trust or configured CAfile. All errors mapped to safe delivery codes; no response/body logs.
- Relay config is a private JSON file path `RELAY_CONFIG_FILE`, with sources {id,token_sha256,destinations,allowed_origins}, aliases {alias:{chat_id,revision}}, and sqlite path RELAY_DATABASE_PATH (default /data/relay/notifications.sqlite). TLS cert/key paths RELAY_TLS_CERT_FILE/RELAY_TLS_KEY_FILE. One bot uses TELEGRAM_BOT_TOKEN from relay environment. Missing TLS/config fails startup; tests can instantiate service/ledger directly without adding insecure production flags.
- Root adds `NOTIFICATION_TRANSPORT=direct|relay`, default direct only to preserve existing configured direct sender until explicit cutover. Relay mode NEVER falls back to direct on absent/broken target. Existing source payload with relay snapshot is never directly sent even if transport later changes.
- Website settings already have notification_relay_target/token/ca_file and notification_destination_alias. Source immutable full request snapshot, snapshot target and relay_notification_id live in existing ENCRYPTED Outbox.payload; no new schema revision needed. Persist snapshot before network; later config changes don't rewrite snapshot. Target/source config changes pause accepted work with safe attention error instead of implicit cross-host resubmit.
- Extend process_once optionally with injected relay client factory for tests; direct send interface/tests remain available. Worker uses Submit until relay ID known, then Get; claim/fence source attempts as current implementation. Remote QUEUED/RETRY_WAIT stay sourcequeued with nextpoll; SENDING running; UNCERTAIN retained; TELEGRAM_ACCEPTED done with message ID; FAILED/rejected/expired lookup source rejected projected HTTPerror. Interrupted poll never triggers Telegram resend at source.
- Feedback notification readiness uses chosen transport. Preserve outward state set/UI; safe error codes only. Explicit technical test follows selected transport, never bypasses configured relay by calling Bot API directly.
- No ORM SQL filters on encrypted Outbox.payload. Metadata queries remain on event_type/state/times. Avoid raw bulk updates to encrypted payload; use property setter under lease/fencing ownership.

### Task 1: Relay and website integration

**Create:** notification_protocol files,notification_ledger.py,telegram_relay.py,notification_relay_client.py,backend/tests/test_notification_relay.py,test_notification_relay_client.py.
**Modify:** notification_worker.py, feedback.py notification projection only, relevant existing notification tests/CLI caller if needed. Root modifies requirements/config/Compose/runbook separately.

- [ ] Write/run failing ledger/service tests source auth/ACL, same-key/conflict/cross-source, accepted-not-sent, lost ACK replay/restart, leased timeout/fencing/429, retention7/30/60 boundaries, future timestamps and message limits/URLs.
- [ ] Generate protocol, implement minimal durable ledger, validation/auth, bounded global intake (5Submit/sec burst10 per-source and10000pending/source), one sender and TLS-only startup. No custom scheduler framework.
- [ ] Add source client and failing integration tests showing SubmitQUEUED does not markdone, later TELEGRAM_ACCEPTED+message_id does; lostSubmit response retries identical saved snapshot; unavailableGet preservesuncertain; no directfallback on mode/config change; ERP/verification email events ignored.
- [ ] Implement source snapshot/poll/state mapping and transport-specific readiness/test helper; keep old directtests explicitdirectmode. Provide ledger backups via SQLite backup API with restore test.
- [ ] Run focused relay/notification/feedback tests and protobuf regeneration selfcheck; no real network send. Full report .superpowers/sdd/2026-10-28-telegram-relay-implementation/task-1-report.md and root-independent review.

### Task 2: Runtime and release integration (root)

- [ ] Pin grpcio/protobuf runtime, grpcio-tools development only, wire transport/target/token/CA environment to API/worker. Add standalone compose.relay.yaml with persistent ledger, read-only config/TLS mounts, existing backendimage and explicit command; relay gets no CRM database credential/PII keys. Create /data/relay ownedbyuid1000 in image.
- [ ] Add example config with hashed placeholder verifier only, deployment/backup/cutover docs, explicit separatehostrequirement and bot credentials migration after cutover. Update original Telegram design to link canonical new contract and transition mode.
- [ ] Independent full-change review and focused fixes, then root integrated backend/frontend tests and local endtoend. No real relayhostprovision or enablement without reachable host/credentials and explicit live-test instruction.
