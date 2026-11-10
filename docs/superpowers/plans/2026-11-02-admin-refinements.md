# Admin refinements implementation plan

> For agentic workers: use superpowers:subagent-driven-development task-by-task. Root coordinates isolated workspace and reviews.

**Goal:** Implement the user's seven explicit email/authentication, request numbering and UI corrections.
**Architecture:** Shared email authentication and reusable AccountAccess, PostgreSQL sequence for canonical feedback numbers, shared frontend feedback formatting with scoped manager list CSS.
**Tech Stack:** Existing Vue/Vite, FastAPI/SQLAlchemy/PostgreSQL/Alembic. No dependencies added.
**Spec:** docs/superpowers/specs/2026-11-02-admin-refinements-design.md

## Global Constraints
- Preserve user IDs/password hashes/roles/sessions, record UUID URLs and immutable receipt/outbox snapshots.
- New staff email is explicitly provisioned; existing missing emails are never invented or implicitly verified.
- Exact feedback format: №12 от 02-11-2026 13:45, Europe/Moscow; global integer across both feedback types.
- Current dirty baseline is the release boundary, not HEAD. File ownership below; no shared edits, commits, deployment or real messages.

## Review Focus
- Telegram private link with no session retains case/order target after email login, registration/reset navigation and role denial.
- Email normalization and throttling identical across credential endpoints; no username fallback or verification bypass; existing sessions survive.
- Migration/concurrent numbering/idempotent replay preserve stable IDs and encrypted historical receipts.
- Fixed columns tolerate long text and 320/390px viewport; labels are understandable Russian.
- Logout/in-flight login transitions don't leave private data visible; removed controls/callout stay absent in all modes.

### Task 1: Backend email identity
**Own:** backend/app/auth.py, users.py, bootstrap.py, manage_staff.py, init_env.py, config.py as needed; backend/tests/conftest.py, test_access.py, test_accounts.py, test_users.py, test_bootstrap.py, test_init_env.py; .env.docker.example, compose.yaml only required defaults. No frontend or feedback files.
- [x] Add focused failing tests for both credential endpoints using normalized verified email, shared throttle/blocked/unverified/username rejection; user-view login=email; no account/session recreation.
- [x] Implement common credential resolver; manager legacy-shaped login accepts email alias only; existing customer input remains email. Keep authorization/security machinery.
- [x] New trusted bootstrap/CLI provisions a valid normalized email (existing ADMIN_LOGIN key can carry email); existing bootstrap restart must never change existing users. No automatic conversion of email-less historical users.
- [x] Adapt affected backend authentication fixtures/tests and record focused test results and rollout implications in root plan task-1-report.md.

### Task 2: Numbering and feedback contracts
**Own:** backend/app/feedback.py, new Alembic0006 migration, backend/app/notification_worker.py only new-message formatting; backend/tests/test_feedback.py, new migration test, test_notification_worker.py/test_notification_links.py as needed; src/feedback.js, src/accounts.js, src/data/demo-feedback-api.js; related JS unit tests. No Vue/CSS/auth files. Shared src/data/demo-api.js owned Task3; request any adapter change from Task3.
**Produces:** exported formatFeedbackReference(number, createdAt) -> exact requested text; isFeedbackNumber(value) positive JS-safe integer (historical snapshot validation may allow nonempty legacy string); FEEDBACK_STATUS_DESCRIPTIONS mapping and readable notification descriptions if useful. Coordinate exact exports with Task3.
- [x] RED tests numeric concurrent allocation/shared types/replay, migration old ordering/empty schema/unchanged receipts/snapshots, numeric search; formatter Moscow day boundary and padding; demo1/2/replay.
- [x] BIGINT positive safe number with schema-aware Sequence usable in metadata.create_all; migrate canonical numbers1..N and nextN+1; preserve UUID and stored snapshots. Downgrade must be deliberate/preserving; document limitations.
- [x] Update validators for new integer plus legacy immutable snapshots, demo allocator and only new notification text; keep saved relay_request untouched.
- [x] Run focused tests and write root plan task-2-report.md.

### Task 3: Frontend authentication and service UI
**Own:** src/App.vue, components/AccountAccess.vue, UsersManager.vue, FeedbackManager.vue, CustomerAccount.vue, styles.css, data/http-api.js, data/demo-api.js; affected HTTP/demo unit tests and browser fixtures as needed. No backend, shared feedback.js/accounts.js/demo-feedback-api.js owned Task2.
- [x] Use one email AccountAccess for anonymous private routes with safe returnTo and no automatic demo admin session; remove username form/toggle/check-session controls. Both API adapter methods resolve the same email login; demo identities login=email. Users UI displays email once, not separate oldlogin.
- [x] Sign-out uses existing sign-in button style and proportions on header/service/account pages. Remove entire 1C preparation callout and its unused CSS; preserve unrelated runtime behavior.
- [x] Consume Task2 formatFeedbackReference in request lists/detail/customerfeedback, fixed manager-only column widths, Russian status descriptions/help and notification meaning. Preserve accessible direct links and responsive behavior.
- [x] Update relevant tests/fixtures; root will run final browser after all files ready. Write root plan task-3-report.md.

### Task 4: Root integration and verification
- [x] Review each task diff against frozen baseline, resolve cross-file expectations and maintain docs/API examples.
- [x] Run full backend and frontend suites once complete, build/check, real disposable-schema migration and concurrent tests.
- [x] Local browser on isolated app/test DB: private Telegram route→email login→samecase, users alias, logout style, removals, column alignment/longtext/mobile, Russian labels and numeric date reference. Only synthetic data/mock delivery.

### Task 5: Review and transfer
- [x] Fresh independent whole-change review against baseline-generated diff and task reports, fix important findings with regression evidence.
- [x] Hash-guard original 206 baseline files and copy only this task's reviewed changes/additions back to shared workspace. Preserve all previous work; no commits/deployment. Verify changed hashes and report exact checks/remaining rollout constraints.
