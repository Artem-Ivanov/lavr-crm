# PDF Catalog Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Implement PDF import now and a tested future adapter port through one stock reconciliation pipeline, with exact fractional units throughout the site.

**Architecture:** File parser and typed adapter port produce normalized snapshots. PostgreSQL persists jobs, row decisions, links and atomic application. A separate admin dialog consumes bounded preview APIs; existing purchase flows gain exact quantities without changing ERP availability policy.

**Tech Stack:** Existing Vue/FastAPI/SQLAlchemy/PostgreSQL; Python Decimal, JavaScript integer thousandths/BigInt; coordinate PDF parsing dependency pinned after real-file verification.

**Spec:** docs/superpowers/specs/2026-11-02-pdf-catalog-import-design.md

## Global Constraints

- Actual PDF input of supplied layout; physical total stock; units шт/кг/л/м; at most three decimal places. No silent rounding quantities or source merging.
- Preserve Product.id, reservations, existing presentations, stored order/receipt snapshots and legacy request digests. Missing source rows do not mean zero.
- Admin-only mutations, CSRF/Origin, bounded uploads/pagination, versioned decisions, replay-safe apply, no production writes or external messages.
- Two source paths share normalization/preview/apply. Real adapter disabled; test adapter exercises the port. Source namespaces and accounting ownership remain explicit.
- Existing ERP checkout gate is preserved. Do not pretend that file import configures 1C.
- Worktree /private/tmp/lavr-crm-catalog-import-20261102 copied from 215-file current dirty baseline; no commits/pushes. Root transfers only task changes with hash guards.
- Dedicated test DB only: postgresql+psycopg://test:test-only-admin-refinements@127.0.0.1:55443/lavr_admin_refinements_test (synthetic local credentials).

## Review Focus

- Exact duplicate signatures with different prices/stocks and bundle components must not overwrite an ordinary product.
- Unit change in an old basket cannot reinterpret pieces as kilograms; old integer idempotency retries remain valid.
- Worker interruption/timeout or malformed page cannot leave a partially applied catalog.
- Reserve change after preview forces a new review; presentation-only changes survive.
- Whole sample has about 24.7k priced rows, absent brands/SKUs and thousand separators; no 24k-row DOM or silent page omissions.

## Task 1: Exact backend quantities

**Owner files:** backend/app/quantities.py (new); catalog.py, orders.py, feedback.py, customer_records.py as necessary; migration 0007; focused backend tests.

**Interfaces:** Product.stock/reserved become Decimal NUMERIC(13,3), normal-unit values unchanged. Product.unit is nullable string enum, stock_observed_at is optional timestamptz. Existing products migrate to шт. Export helpers quantity_decimal(value), quantity_milli(value), quantity_json(value), validate_unit_quantity(value, unit), line_total_kopecks(price, quantity). Missing expectedUnit means шт; omission must preserve old canonical dumps/digests. New snapshots unit/lineTotalKopecks; old snapshots untouched.

- [x] RED: fractional reserve/cancel/rounding/units and legacy digest tests on real PG.
- [x] Implement helpers, model/migration, all order/conversion readers/writers and product serializers (honest blank SKU/brand). Protect unit changes and freshness using actual observation for imported products.
- [x] GREEN: focused + existing backend tests; migration old data and precision bounds.
- [x] Scoped spec/quality review and fix findings.

## Task 2: PDF and future adapter import pipeline

**Owner files:** new backend/app/catalog_sources.py, catalog_pdf.py, catalog_imports.py, catalog_import_worker.py; migration0008; requirements; backend tests. main.py/alembic imports registration may be edited here; Task1 does not own these files.

**Interfaces:** Read Task1 helpers/model contract. Define typed CatalogSourceRow/CatalogSnapshot and CatalogSourceAdapter port. PDF and fake adapter invoke one preparation pipeline. Disabled real adapter never produces successful import or ERP-connected state. Stable adapter source_product_id mandatory; file identities use confirmed unique signatures. Source accounting owner serializes writes.

**Admin API contract:** base /api/admin/catalog/imports. GET /capabilities returns file limits/units and disabled adapter status; GET base lists last packages; POST base multipart file, observedAt, priceConfirmed=true + UUID Idempotency-Key creates queued package. GET /{id} with offset/limit/filter/query returns package metadata/version/summary and rows={items,total,offset,limit}. Each row exposes source position/page, sku/brand/name, unit, selected, action, proposed stock/price, before accounting snapshot, targetProductId and readable issues. PATCH /{id}/decisions with If-Match edits explicit rowIds or filter+query scope: selected, unit, categoryId, resolution(new/link/skip), productId, acknowledgeStandalone. POST /{id}/refresh with If-Match rebuilds baseline; POST /{id}/apply with If-Match, UUID Idempotency-Key and acknowledgeSkipped bool applies selected validated rows atomically and returns saved report. Report has createdCount,updatedCount,unchangedCount,skippedCount,appliedAt,observedAt. Root may align minor DTO details with owner before UI integration.

- [x] RED: normalized snapshot/source identity/row parsing/security/atomic apply/idempotency tests.
- [x] Install/pin parser in isolated env, verify sample coordinates and every page; measure wall time and peak RSS. Limits5MiB/400pages/50krows; no OCR/arbitrary layouts.
- [x] Implement persisted worker queue/lease and bounded subprocess, private raw storage/retention7d and detailed rows30d, reports/mappings retained. A single worker, no network PDF processing.
- [x] Implement preview decisions, bulk units/category, review-only ambiguous duplicates/bundles, safe subset atomic apply, reserve/baseline conflict and observation checks. New hidden category/card support; blank fields honest; preserve presentation.
- [x] Test typed adapter snapshot path through same preparation/apply, missing adapter failure, namespaces and owner collision.
- [x] GREEN: parser unit/real sample checks + PG integration tests and scoped spec/quality review.

## Task 3: Fractional client quantities

**Owner files:** new src/quantity.js; existing cart/draft/order/accounts/data/http-api.js/data/demo*.js and product/cart/account/feedback UI; focused tests. Do not edit CatalogManager.vue/styles.css (root owns import UI integration); append styles to a new quantity CSS file if required. Root adds import methods to HTTP adapter only after this task returns.

**Interfaces:** quantity/stock stay normal-unit JSON numbers; unit шт/кг/л/м; missing unit legacy шт. All calculations normalize to integer thousandths, monetary products use BigInt and per-line HALF_UP. New requests expectedUnit only where needed while legacy integer hashes remain unchanged. Product.unit may be null in admin but public purchasable items must have confirmed unit. API product/line validators accept honest absent SKU/brand and optional historical unit/lineTotalKopecks.

- [x] RED: .001/.955/9.785 amounts, steps, sum/overflow, stale unit, legacy drafts/digests and customer/conversion DTOs.
- [x] Implement helper and every quantity consumer, demo adapter, exact subtotal displays, input steps/units and per-unit price. Cart count means distinct positions, not mixed-unit sum.
- [x] GREEN: npm test/check, API/demo builds; scoped spec/quality review.

## Task 4: Administrator dialog and client import API

**Owner:** root. New CatalogImportDialog.vue, src/catalog-imports.js or data/http-catalog-import-api.js as needed; CatalogManager.vue/styles.css; API module attachment after Task3; focused API tests/browser flows.

- [x] RED: upload/preview validation, version/idempotency, failure and actor-switch behavior; browser initially cannot open dialog.
- [x] Add button/native dialog in established styles, file/date/price acknowledgement, source readiness, progress, history/reopen, summary/filter/pagination, before→after stocks/reserves/prices, bulk/per-row units/category/resolution, refresh/apply report and explicit skips.
- [x] Guard async actor generation, duplicate apply, expired session, disabled adapter and malformed/oversize PDF; preserve keyboard focus/Escape/mobile scrolling.
- [x] GREEN: real local API browser upload→review→units→apply→report, repeated import, operator/partner rejection and UI at desktop/mobile widths.
- [x] Scoped review of UI/contract.

## Task 5: Integration, operations and whole change review

**Owner:** root. compose.yaml/.env examples/docs/openapi and deployment documentation as required; parser worker command uses current backend image and data DB, no live deploy.

- [x] Register all models in app/migration/test imports; add worker with bounded resources without enabling Telegram/SMTP.
- [x] Run combined backend/frontend suites, migrations, API/demo build and real browser; validate sample source key fields and exact fractions against original PDF. Keep reports not full product dumps.
- [x] Generate scoped diffs against dirty baseline; independent final spec/security/data-integrity review; fix material findings and recheck affected paths.
- [x] Hash-guard transfer tested changes to root; prove untouched baseline preserved; record exact results and local run instructions. No commit/push/production import or deployment.

## Preflight rulings

| Pair/task | Shared boundary / check | Ruling |
|---|---|---|
| 1↔2 | Product model and quantity helpers | Task1 owns catalog.py/migration0007; Task2 only consumes and owns new import files/migration0008. |
| 1↔3 | JSON quantity/unit/legacy snapshots | Preserve normal-unit numbers; missing unit means шт. Unit mismatch must force review. |
| 2↔4 | Import API | Task2 owns server DTOs; root pins/aligns client after implementation report; no shared file writes. |
| 3↔4 | HTTP adapter | Task3 completes quantity edits first; root appends import methods afterward. CatalogManager/styles reserved to root. |
| 2↔5 | Worker/compose | Task2 supplies CLI loop and constraints; root wires compose only after contract known. |
| 1 | Exact quantities vs old digests | Internal Decimal never changes canonical legacy model dump. |
| 2 | PDF no permanent IDs/units | Unknown identities/units reviewed, no guessed values or silent row drops. |
| 3 | New helpers vs demo/cart readers | Every consumer changed in same task; old browser state supported. |
| 4 | Safe subset vs whole-file failure | Explicit row skips allowed; unknown page/template fails package. |
| 5 | Dirty root | Snapshot/hash baseline rather than HEAD; no unrelated changes lost. |

Independent files permit parallel implementation per host multi-agent instructions; staged joins above prevent shared-state collisions. User has repeatedly directed implementation, most recently clarifying the two inputs; do not request another routine implementation permission. No commits are requested.
