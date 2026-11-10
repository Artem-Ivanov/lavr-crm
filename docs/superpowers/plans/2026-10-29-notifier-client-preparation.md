# Notifier Client Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prepare a typed client boundary for external-repository internal-notifier and canonical safe CTA links without changing delivery semantics or enabling sends.
**Architecture:** Keep notifications.v1.NotificationRelay, RelayClient and the encrypted outbox; introduce only request/button TypedDicts and a structural Protocol using the existing protobuf receipt. Backend URL generation follows the same route fixture as the approved frontend router. Legacy server files remain frozen reference until external handoff.
**Tech Stack:** Python3.13, typing, existing grpcio/protobuf/pytest/PostgreSQL. No dependencies added.
**Spec:** docs/superpowers/specs/2026-10-29-internal-notifier-transition-design.md and2026-10-29-direct-links-design.md, user approved29.10.2026.

## Global Constraints
- No server implementation here, external repo creation, live Telegram/SMTP, flag activation, production mutation, deployment or Git commit/push.
- Worktree /tmp/lavr-crm-notifier-links-20261029 contains all prior dirty work; task diffs must use a per-task before snapshot, not whole HEAD.
- Keep protobuf package/methods/fields/states and stored relay_request/metadata/event_type feedback_telegram; no duplicate receipt DTO, bus or SDK.
- Keep legacydirect compatibility, immutable snapshots, idempotency, leases, UNCERTAIN persistence and no direct fallback for pinned jobs.
- New URI fields never contain contacts/message/OTP/session/CSRF or arbitrary external redirect. Current actual feedback events retain their exact URL.
- Tests use dedicated QA DB only; root provides /tmp/lavr-crm-notifier-links-test/run-tests.py. No preview/production DB.

## Review Focus
- An unknown transport configured through env/monkeypatch must never route direct or enable a technical send.
- Changed endpoint/credential, accepted priorjobs and transient CA failure retain existing safe reconciliation/uncertainty.
- Opaque IDs with Unicode and ?&#% roundtrip exactly once; duplicate/unsupported query params cannot become unsafe CTA URLs.
- Existing code/tests import RelayClient and use injectable relay_client_factory; preserve both interfaces.
- Text/URL shared contracts remain PII-free, and the client module imports no CRM business models/server implementation.

### Task 1: Typed external client boundary and trusted links

**Files:** Create backend/app/notifier.py, backend/app/notification_links.py, backend/tests/test_notification_links.py. Modify backend/app/notification_relay_client.py, notification_worker.py, config.py/main.py only for minimal transport validation, telegram_cli.py only if shared validation requires it. Tests test_notification_worker.py/test_notification_relay_client.py/test_runtime_config.py; docs/telegram-relay-runbook.md andREADME only the notifier handoff paragraphs if needed (root coordinates shared README later).

**Interfaces:**
- Produces NotificationButton(TypedDict){label:str,url:str}; NotificationRequest(TypedDict){request_id:str,request_created_at:str,destination_alias:str,text:str,button:NotRequired[NotificationButton]}.
- Produces NotifierClient(Protocol) with submit(request:NotificationRequest)->notifications_pb2.NotificationReceipt, get(notification_id:str)->same, close()->None. No runtime-checkable wrapper/inheritance necessary.
- RelayClient structurally satisfies this protocol. Annotate the existing factory/JSON snapshots while preserving names and behavior.
- Produces site_link(page:str, parameters:dict[str,str]|None=None)->str in notification_links.py. Uses trusted site_origin(); allowlisted pages and wire keys from tests/fixtures/navigation-v1.json. No auth/returnTo parameters in notification CTAs. Reject unknown keys/duplicate or invalid IDs by bounded DeliveryError; no customer-controlled full URL input. Existing feedback URL stays {origin}/#crm-feedback?case={ID}.
- Canonical query key order: catalog category then product; home section OR publication; crm order; crm-feedback case; account-orders order; account-feedback case; admin-users user; crm-content tab then publication. IDs1–128characters excluding controls and . or .., encoded once with standard URL encoding. CMS publication requires tab=publications. No IDs in top-level defaultlist pages.
- Transport remains exactly direct|relay. Unknown values reject API startup and fail closed on worker/test CLI paths, including monkeypatched settings; preserve already safe disabled behavior. Reuse a small validation helper if needed, avoid renaming env or wire protocol.
- Consumes shared tests/fixtures/navigation-v1.json frozen by root/L1. Only URL examples with public/staff/non-auth destinations apply to Python.

- [x] Write RED tests: legacy feedback URL equality; stafforder/menu route examples/Unicode ID; trusted invalidorigin/unsupported/duplicate parameter rejection; unknown transport cannot use direct sender; existing RelayClient fake injected in worker still succeeds.
- [x] Run narrow RED with QA runner; report actual failure before implementation.
- [x] Implement minimal types/helper/wiring and shared validation; update only existing feedback CTA generation, no new automatic events.
- [x] Run relevant notifier/worker/client/runtime suites and gitdiffcheck. Preserve current uncertainty regression and snapshot expectations.
- [x] Self-review and save task report/diff with exact RED/GREEN and limitations. No Git commit; root later guarded-copies reviewed files.
