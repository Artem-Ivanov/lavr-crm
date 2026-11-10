# MIRZAP67 Accounts and Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver email-activated password accounts, private customer orders/feedback, admin users management, protected PII and the confirmed partner/operator/admin access policy.

**Architecture:** One User principal and lavr_session serve customer and operational projections. A fixed role enum keeps admin full access; server capabilities drive navigation without disclosing role labels outside admin APIs. AES-GCM protects all persisted PII copies; transactional email outbox uses an independent worker. Preserve guest ownership and legacy retries.

**Tech Stack:** Existing FastAPI/SQLAlchemy/PostgreSQL/Alembic, Vue/Vite, Argon2 and Python SMTP; add cryptography==48.0.1 for authenticated encryption.

**Spec:** ../specs/2026-10-28-customer-accounts-design.md and ../specs/2026-10-28-user-permissions-design.md. Relay is a separate subsystem/plan.

## Global Constraints

- User.role is exactly partner|operator|admin; signup sets partner; admin automatically includes operator.
- Roles and account administration status are returned only by admin users APIs. Session capabilities are operate/editSite/manageUsers booleans, not user.role.
- One opaque HttpOnly Secure/SameSite=Strict lavr_session cookie at Path=/api, 8 hours. Test environments explicitly disable Secure.
- Guest records remain unowned. No contact matching or automatic guest claims. Presented invalid/expired auth cookies never silently create guest records.
- Registration requires phone/email/password; code activates email. Initial login email/password; phone remains unverified/nonunique. Password 12..256 characters, no trimming.
- OTP 6 digits; code TTL 10 minutes; flow lifetime 30 minutes; maximum 5 failed checks per flow, preserved across resend; cooldown 60 seconds; 5 mails/email/hour,20/IP/hour;20 checks/email/hour,60/IP/hour.
- Purpose-bound HMAC codes; browser continuation; atomic consume. Unique confirmed email includes blocked accounts. Signup/reset never clear a block.
- PII_ENCRYPTION_KEYS is a JSON key-id=>base64url 32-byte-key map; PII_ACTIVE_KEY_ID chooses writer; PII_INDEX_KEY is separate base64url 32-byte key; CHALLENGE_SECRET is separate high-entropy secret. No generated production defaults.
- Encryption AAD binds table/id/field/format; fresh 96-bit nonce. No plaintext fallback for nonempty encrypted fields. Legacy migration explicitly encrypts all copies.
- Source-order/feedback idempotency persists actor and digest_version; old guest v1 digest unchanged, new v2 binds actor. Verify owner before receipt.
- No production env, DB/media volumes, emails, Telegram messages or paid calls in this implementation. Dedicated PostgreSQL *_test only. Existing public ERP_NOT_CONFIGURED behavior remains.

## Review Focus

- Existing blocked email cannot bypass admin block by signup/reset; assert in Task 2.
- A stale cookie or stale async response after identity change cannot produce a guest order or show prior user's data; Tasks 3/4.
- Legacy guest retries after encryption/schema migration retain original receipts and ownership; Tasks 3/5.
- Concurrent last-admin demotion/block cannot lock out all administrators; Tasks 1/5.
- PII leaks through conversion snapshots, notes, ERP/email outbox or logs even when profile encrypted; Tasks 2/3/5.

## File ownership and shared interfaces

Root owns config.py, main.py, requirements, Compose/env/docs and integration migration coordination. Worker 1 owns auth.py,users.py,bootstrap.py,manage_staff.py,test_access/test_bootstrap/conftest and new user tests. Worker 2 owns crypto.py,orders.py,feedback.py,integration.py,customer_records.py,PII/owned-record tests and migration 0004. Worker 3 owns frontend src/tests JS/browser. Registration worker later owns accounts.py,email_worker.py,challenge models/tests and migration 0005. Root is the only shared-plan writer. Parallel workers do not edit each other's files.

Interfaces fixed here:
- crypto.py: seal_json(value, *, table, record_id, field)->dict; open_json(envelope, *, table, record_id, field); seal_text/open_text with same keyword context; lookup_digest(kind, normalized_value)->str. Blank optional text may stay empty sentinel; nonempty plaintext rejected. Crypto worker supplies these early.
- auth.py: User ORM(table users) with stable id, encrypted legacy login/name properties (nullable login; empty default name), unique login_lookup, nonunique phone_lookup,role,is_active,password_hash,auth_version(default1),created_at,email_lookup(unique nullable),email_verified_at,profile_ciphertext(JSONB). User.profile is decrypted {email,phone}; setters get an id before seal, calculate unique email_lookup and nonunique phone_lookup. Legacy login is case-sensitive trimmed, HMAC kind legacy_login; login_ciphertext/name_ciphertext are Text columns, no plaintext duplicates. AuditEntry.details JSON records only safe role/active changes. create_session(db,user,request,response)->UserSession does not commit; signup activation owns transaction. UserSession ORM with user_id/token_hash/expires_at/auth_version. Preserve AuditEntry staff_id column referencing users.id to avoid needless history-column churn.
- auth.py exports get_current_user (any active/session-valid principal), get_current_staff (operator/admin), require_mutation (operator/admin+Origin/CSRF), require_user_mutation (any valid User+Origin/CSRF),require_admin/require_admin_mutation,check_origin,hash_password,record_audit. Session projection session_response(user,session) includes user:{id,name,email,phone,emailVerified,phoneVerified:false,profileComplete},capabilities:{operate,editSite,manageUsers},csrfToken,expiresAt; no role/isActive.
- auth.py exports guest_only(request,db=Depends(get_db)): no cookie permits guest; invalid presented cookie401; valid cookie409 CUSTOMER_FLOW_REQUIRED. get_current_customer validates full verified profile, otherwise409 PROFILE_REQUIRED; require_customer_mutation adds Origin/CSRF.
- Canonical session routes GET/POST/DELETE /api/customer/session (POST email/password); legacy /api/manager/session uses legacy login/password over same principal/cookie. Signup/reset/profile-completion routes in accounts.py per spec. Activation returns same session DTO.
- users.py router: /api/admin/users GET list/detail, PATCH {id}/permissions body {role}, PATCH {id}/status body {isActive}, If-Match,version/auth_version. list filters q full email/phone/legacy login; no secrets; detail ETag. Last admin check serialized with PG advisory transaction lock; auth_version bump revokes prior sessions.
- customer_records.py router: GET/POST /api/customer/orders and /feedback plus GET detail. POST order {fulfillment,items,name?}; POST feedback {type,message}. Contact/owner server-only. Customer DTOs exactly spec; operator manager feedback DTO gains boolean canManage calculated by server for FeedbackManager instead of role logic.
- Frontend HTTP methods: getSession/login({login,password})/logout preserved for legacy entry, getUserSession/loginUser({email,password}), signup({email,phone,password}),activate({flowId,code}),resendCode({flowId}),requestPasswordReset({email}),confirmPasswordReset({flowId,code,newPassword}),startProfileActivation({email,phone}),confirmProfileActivation({flowId,code}),getOwnOrders/getOwnOrder/createOwnOrder,getOwnFeedback/getOwnFeedbackCase/createOwnFeedback,listUsers/getUser/updateUserRole(id,role,version)/updateUserStatus(id,isActive,version). One CSRF/session generation across all identity transitions.

### Task 1: Unified identity, role gates and admin users

**Files:** auth.py,users.py,bootstrap.py,manage_staff.py; tests/conftest.py,test_access.py,test_bootstrap.py,test_users.py.

- [ ] Write failing API tests proving ordinary session omits role, partner denied operational/admin APIs, operator denied users/content, admin inherits operator; run focused pytest and record expected red.
- [ ] Implement User/UserSession and preserve stable legacy login/hash; update staff FK users in cooperating worker's feedback model. Add canonical session routes and capabilities projection, public Origin/CSRF policy and no-cookie/expired-cookie guest guard.
- [ ] Implement users API, enum validation/If-Match/audit, block/session revocation and serialized last-active-admin protection. Bootstrap only first install; no permission resurrection; CLI creates operator safely.
- [ ] Update relevant existing fixtures/imports to User, keep manager_headers legacy login fixture pointing to role operator. Add tests stale sessions/blocked users/last-admin race and admin full operational access.
- [ ] Run focused tests then full backend suite after other modules ready; hand report to root for independent review. Do not commit partial shared-worktree edits.

### Task 2: Verified registration, recovery and independent mail worker

**Files:** accounts.py,email_worker.py,tests/test_accounts.py,test_email_worker.py,alembic/versions/0005_account_challenges.py. Root registers router/config/Compose.

- [ ] Write/run red tests signup→code→activation→session with chosen password; duplicate active/blocked email and unsafe public roles rejected. Read code only from captured in-process test SMTP stub; production API/logs never returns code.
- [ ] Implement UserChallenge and durable throttles, canonical phone/email validation, independent pending flows/continuation, HMAC generation/atomic consume, signup/reset/legacy-profile-completion purposes and exact limits from Global Constraints.
- [ ] Implement email worker selecting only customer_verification_email, encrypted payload, challenge generation/TTL validation, bounded lease/backoff, TLS SMTP transport and safe errors, stale/terminal payload cleanup.
- [ ] Add independent migration after 0004; registration flag defaults false. Missing SMTP gives safe503; disabling new signup does not disable existing login/recovery when SMTP configured.
- [ ] Verify replay/expiry/resend/attempt limits, concurrent activation, password-reset revocation, blocked-email invariants, fake-SMTP failure/restart/expired generation and no plaintext code in raw DB. Run focused tests and report.

### Task 3: All PII encryption and private customer records

**Files:** crypto.py,orders.py,feedback.py,integration.py,customer_records.py,tests/test_crypto.py,test_customer_records.py,test_orders.py,test_feedback.py; migration0004. Own crypto/backfill helpers called by migration.

- [ ] Write/run red tests AEAD tamper/row-binding/key absence and raw SQL absent PII in profile/order/feedback/notes/conversion/ERP payload. Publish fixed crypto interfaces to identity worker early.
- [ ] Implement vetted AES-GCM envelopes and HMAC normalized exact lookup; encrypt model properties with stable ID AAD and no nonempty plaintext fallback; add ownership/index/digest-version fields. Preserve transactional build_order/feedback and ERP state invariants.
- [ ] Factor shared order/feedback creation minimally; add owner-filtered own DTO routes, profile-bound POST and optional name only authenticated path; enforce guest_only for existing public routes.
- [ ] Implement actor-sensitive new attempts, legacy v1 guest replay, conversion owner propagation, full exact-contact search and server-derived feedback canManage. ERP operational endpoints allow operator/admin.
- [ ] Migration0004 renames identity/FKs preserving IDs, role manager->operator, invalidates sessions, adds ownership/digest fields, backfills all encryption including notes/snapshots/outbox, replaces obsolete plaintext resolution check. Requires keys and maintenance mode; no destructive volume/database reset. Preserve old guest digest exactly.
- [ ] Run encryption/ownership/idempotency/access tests and seeded migration upgrade/restore tests; report to root. No production backfill or external network.

### Task 4: Login/signup, customer account and user administration UI

**Files:** src/App.vue,src/data/http-api.js,src/data/demo-api.js,src/data/demo-feedback-api.js,src/feedback.js,new focused components/helpers,src/styles.css; affected JS/browser tests.

- [ ] Write/run red HTTP-client tests canonical session/capabilities, generation handling, owner endpoints and role admin-only payloads; update relevant mocks to canonical contract rather than accepting old leaked role responses.
- [ ] Replace public CRM header link with login/signup/account navigation; preserve legacy employee entry on login. Build signup/activation/resend/reset/profile completion flows with loading/error states and no credentials persisted in storage.
- [ ] Show private orders/feedback lists/details and profile contacts; checkout auto uses profile/server owner, keeps fulfillment inputs and no mandatory name; preserve guest forms, scoped retry keys and explicit re-login on expiry.
- [ ] Add admin user list/details, role selection and block/unblock with versions; show raw role labels there only. Existing CMS/operations tabs use capabilities and server canManage; operator blocked from CMS/users, admin sees all.
- [ ] Adapt demo mode with clear simulation labeling and no false real email delivery; update all callers/fixtures/strict DTO validators. Preserve MIRZAP67 branding/media and completed footer cleanup.
- [ ] Run npm test/check, API+demo builds and focused browser scenarios partner/operator/admin, two-user isolation, slow-response identity switch, mobile320/390 and keyboard forms. No live emails or changes to public site.

### Task 5: Integrate, verify and hand over local release candidate

**Files:** root config/main/requirements,alembic/env.py,compose/env examples,docs/api.openapi.yaml,README/runbook; changes from tasks above.

- [ ] Root config provides exact crypto keys and SMTP env names, disabled-by-default signup, independent verification-emails profile; register all models/routers and fail closed on missing crypto configuration.
- [ ] Run actual isolated PostgreSQL full backend suite and all frontend tests/builds after reviewed task fixes. Run migration0001→0003→0004→0005 on a populated disposable *_test DB including legacy guest retries, then runtime tests over migrated schema.
- [ ] Browser end-to-end against local API with actual PostgreSQL and test SMTP capture: registration/activation/login, customer-owned feedback and order with test-only configured ERP, own status, admin grants/demotion/block/last-admin, operator restrictions and no role leakage. Production ERP stays disabled.
- [ ] Independent code/security review of full diff; fix material issues and rerun only affected checks. Merge reviewed source into original workspace guarded against intervening edits; preserve all pre-existing source/config/media changes. No forced reset or blanket git add.
- [ ] Provide concrete verified local candidate and explain SMTP setup and relay reachability required for production. Deployment/backfill waits until required provider/keys/backup are ready; no half-configured registration is presented as working.
