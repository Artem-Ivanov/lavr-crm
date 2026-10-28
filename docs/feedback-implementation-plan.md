# Обращения LAVR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Public contact/request form, durable CRM processing, Telegram URL notifications and atomic conversion to a normal order.
**Architecture:** Existing Vue/FastAPI/PostgreSQL patterns, one feedback module, one isolated notification worker, existing Order transaction logic shared by both creation routes.
**Tech Stack:** Vue 3, Vite, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, Alembic; no new runtime packages.
**Spec:** [feedback-design.md](feedback-design.md), approved 20 October 2026.

## Global Constraints

- At least one phone/email/messenger, all supplied contacts validated; type contact/request; request text required.
- Manager/admin reads; owner/admin mutates claimed cases; Origin/CSRF/If-Match, audit, terminal states enforced server-side.
- Feedback intake independent of Telegram and ERP; real order conversion preserves all existing ERP/catalog/reservation checks.
- No contacts in browser storage, no secrets in frontend/logs, no public private-case reads.
- Original buyer cart and checkout retry state remain independent; no dependencies, no remote publication/messages without configured credentials.

## Interfaces shared by all tasks

Public body: `{type:"contact"|"request",name,phone,email,messenger:{service,address},message}`; empty strings for absent fields.
Public receipt: `{id,number,createdAt,state:"accepted"}` (demo may add `demo:true`).
Manager case: `{id,number,createdAt,updatedAt,type,name,phone,email,messenger,message,status,assignedTo:null|{id,name},version,resolution,order:null|OrderReceipt,history:[{id,createdAt,staff:null|{id,name},action,text,contactMethod}],notification:{state,code}}`.
States: new/in_progress/converted/closed. Notification state: queued/running/done/error/uncertain/not_configured; code null or safe uppercase code. History contactMethod empty string/phone/email/messenger.
List: existing `{items,total,offset,limit}` envelope, manager case items (full shape accepted for simplest shared validation).
Mutations status/notes/conversion return manager case; conversion success replay returns stored manager response (same original version/receipt).
API methods: `submitFeedback(body,key)`, `getFeedback({query,status},{offset,limit})`, `getFeedbackDetail(id)`, `updateFeedbackStatus(case,{status,resolution})`, `addFeedbackNote(case,{text,contactMethod})`, `createFeedbackOrder(case,orderBody,key)`.
Retry helpers: `feedbackKey(body,scope="submission",storage=sessionStorage)` / `clearFeedbackKey(scope,storage)` with separate `lavr.feedback-attempt.v1:<scope>` technical keys; no body storage. UI conversion scope `order:<case-id>`.
Routes: `/#crm-feedback`, `/#crm-feedback?case=<UUID>`, `/#crm?order=<UUID>`; preserve through login and reload.
Backend model interface for worker: `app.feedback.Feedback` with `id`, `number`, `type`, `created_at`; Outbox event id equals Feedback.id and type feedback_telegram.
Shared order helper: `orders.build_order(db, body: CreateOrderRequest) -> Order`, no commit or OrderAttempt; creates normal order/reservations/order_export. Public checkout keeps its original idempotency.
Settings owned by root: `telegram_bot_token`, `telegram_chat_id`, `public_site_url`, `trusted_proxy_hosts` (comma-separated hosts, default backend Docker proxy ranges configured explicitly).

## Review Focus

- Old successful public replay after private conversion still returns original public receipt: backend task test.
- Two managers claiming/closing/converting together cannot create duplicate orders: backend transaction tests.
- A slow response after navigation/logout cannot overwrite another case or expose private data: UI browser test.
- Worker process inherits an incompatible HTTP healthcheck and can overwrite a reclaimed lease: root worker/config tests.
- Email/messenger-only contact conversion needs real name/phone, and ERP failure preserves case and buyer cart: UI/backend tests.

## Task 1 Backend feedback and order transaction (agent backend)

Files: new backend/app/feedback.py, backend/tests/test_feedback.py; modify orders.py, main.py, alembic/env.py and tests/conftest.py. Do not modify config.py, integration.py or migration (root owns).
- [x] Write/run failing tests for public contact alternatives, malformed input, idempotent receipt and pending outbox, protected list/detail, notes, claiming/closure/version/ownership.
- [x] Add Feedback/FeedbackNote/FeedbackThrottle models, PostgreSQL counters and optional-notification projection; reuse auth/common/audit and shared transaction logic.
- [x] Extract build_order with all callers preserved; implement POST /manager/feedback/{id}/order with same CreateOrderRequest, independent scoped attempt state on feedback, atomic linked order and replay before stale version/ERP.
- [x] Run backend tests with TEST_DATABASE_URL on the isolated test PostgreSQL; report red/green and review constraints.

## Task 2 Frontend data/domain/retry (agent data)

Files: src/feedback.js, src/feedback-attempt.js, src/data/http-api.js, src/data/demo-api.js, optionally src/data/demo-feedback-api.js; tests/feedback.test.js, feedback-attempt.test.js, http-feedback-api.test.js, demo-feedback-api.test.js. No UI/backend files.
- [x] Write/run failing tests for contact validation, payload/response trust boundaries, separate retry scopes, CSRF/version headers and demo no-contact-storage behavior.
- [x] Implement fixed interfaces above, checked HTTP schema and immutable public receipts. Demo feedback lives in memory; demo conversion creates linked normal demo order via existing createOrder without touching cart.
- [x] Run targeted Node tests and existing tests; report red/green and API contract.

## Task 3 Vue UI and routing (agent UI)

Files: src/App.vue, src/components/LandingPage.vue, src/components/FeedbackForm.vue, src/components/FeedbackManager.vue, src/styles.css, tests/browser-feedback.mjs. No provider/domain/backend files.
- [x] Add meaningful browser regression that fails absent form/deep route, using existing Chrome harness patterns.
- [x] Implement common public dialog, contact controls/errors/draft preservation, footer/home actions, CRM list/detail/history/claim/closure/notes and local order preparation via agreed API methods.
- [x] Extend hash parsing and auth target preservation for case and order IDs, invalidate stale responses, preserve dirty guards, keyboard/focus and 320px layout. Buyer cart/checkout unchanged.
- [x] Browser scenarios cover public submission, manager deep-link login, status/note/closure, order preparation and retained 503 inputs, stale response/navigation and mobile controls.

## Task 4 Durable Telegram worker/config/migration (root)

Files: new backend/app/notification_worker.py and backend/tests/test_notification_worker.py; integration.py/config.py, new Alembic 0002, compose.yaml, .env.docker.example, proxy template.
- [x] Write/run failing tests for claim/filter/retry/reclaim/done and plain-text URL button payload with mocked transport; no actual Telegram sends.
- [x] Implement one synchronous worker: bounded request timeout, row lease/CAS finish, exponential persisted retry and 429 retry_after, one channel request/sec. Build from trusted configured origin, no contact/token logs, missing config leaves queue pending.
- [x] Register new nullable outbox fields, settings and trusted X-Real-IP boundary, additive migration from models, worker Compose command/dependencies/restart/healthcheck disable.
- [x] Verify Alembic upgrade/check against isolated DB and Compose config without printing resolved secrets.

## Task 5 Integration, documentation and local delivery (root + fresh reviewer)

Files: docs/api.openapi.yaml, README.md, approved design status, selected .planning ledger.
- [x] Independently review combined diff for spec/security/concurrency and fix actionable findings.
- [x] Run full backend and Node suites, check/build API+demo, new browser suite and existing relevant browser regressions.
- [x] Rebuild/start local Compose with existing env/volumes, verify healthy DB/API/UI, public new inquiry -> staff view/status/note -> expected ERP refusal without data loss. Use explicit synthetic test data only.
- [x] Update API contract/run instructions/status, keep services available at 127.0.0.1:8080; report Telegram needs bot token/channel/reachable URL and 1С conversion limit.

## Execution decisions

User explicitly approved the written project and requested implementation in this session; continue without another permission question. Parallel work uses the interfaces above and disjoint files. Work on local feature branch in the user workspace so the existing Docker build updates their test site; unrelated tracked pyc is preserved. No push/merge/publication.
