# Direct Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make menu destinations and request cards addressable across reload/new tab/login with existing access checks.
**Architecture:** Small explicit hash parser/builder, route-driven public/modal/auth state in App, route props/events for existing private components. Preserve hash names and HTTP APIs; no routing library, new backend endpoints or storing PII.
**Tech Stack:** Existing Vue3, URLSearchParams, node:test, Playwright. No new dependencies.
**Spec:** docs/superpowers/specs/2026-10-29-direct-links-design.md, approved29.10.2026.

## Global Constraints
- Worktree /tmp/lavr-crm-notifier-links-20261029; preserve prior dirty source; report task-only before/after diff. No deploy/production/third-party tracker/Git commit/push.
- Keep old #crm?order and #crm-feedback?case, customer ownership/roles and ordinary guest semantics.
- Public IDs1–128characters/no controls/. or ..; decode once. Do not make them UUID-only.
- Existing modal focus/late-request guards/identity privacy/dirty-navigation confirmation remain valid.
- No passwords/contact/message/search/OTP/CSRF/session into route or storage. New returnTo only canonical internal non-auth hash.

## Review Focus
- Unknown, duplicated, control/overlong and Unicode/percent encoded route values never fetch wrong detail or redirect externally.
- Reload/newtab/login/profile completion returns to requested detail, including before list data loads and after stale session recovery.
- Modal close/direct-entry/back/forward/reopen remain in sync with URL, late old responses cannot replace new selection.
- Dirty CMS/feedback navigation cancel preserves both state and URL; route updates must not mutate local editor before parent accepts.
- Partner never fetches service data; foreign/guest customer IDs404; public missing/hidden content remains unavailable without private APIs.

### Task 1: Explicit shared route contract

**Files:** Create src/routes.js, tests/routes.test.js. Shared fixture tests/fixtures/navigation-v1.json is written/frozen by root before parallel tasks; may extend it only after notifying root/N1. No App or component edits in this task.

**Produces:**
- parseRoute(hash:string)->normalized object {page,caseId,orderId,categoryId,productId,section,publicationId,userId,tab,returnTo,valid}. String fields default'', tab defaults'site'; blank hash/home are valid.
- routeHash(route:object)->canonical '#...' string; invalid internal data throws TypeError. Unknown properties omitted, allowed query fields encoded once in fixed order.
- safeReturnTo(value:string)->canonical '#...' or ''. Accept only single internal allowlisted non-auth destination; reject external/protocol-relative/path URLs, controls, unknownpages, recursive returnTo and auth-page targets.
- targetHash(target:object|null)->canonical hash or null for four existing CMS target types catalog/category/product/section. No generic dispatcher.
- Pages remain home,catalog,cart,crm,crm-catalog,crm-content,crm-feedback,admin-users,login,signup,activate,password-reset,account,account-orders,account-feedback.
- Query fields bypage: catalog category->categoryId and product->productId (both allowed to retain category context); home section or publication (mutuallyexclusive); crm order; crm-feedback case; account-orders order; account-feedback case; admin-users user; crm-content tab=site/publications/media plus publication only forpublications; authpages returnTo; account returnTo only forprofile continuation. An embedded returnTo itself cannot be a return destination.
- Duplicate recognized params/malformed encoding/invalid supplied IDs/unsupported combinations set validfalse and clear detail identifiers (keep known safe page, default home forunknown). Unknown params drop in canonical URL. An invalid returnTo is discarded without breaking valid auth page.
- Parameters names follow existing wire spellings. Fixed serializationorder listedabove; URLSearchParams handlesencoding. IDs may include spaces/Unicode/?&#% but not ASCII controls/DEL/empty/. or ..; length128 Unicode code points (matches Python API, not UTF16 length). Canonical escaping is URLSearchParams: spaces as+, tilde as%7E, asterisk literal*. Full normalized builder empty default strings mean absent fields; explicit empty parsed query values are invalid.

- [x] RED node tests roundtrip all fixture cases, legacy URLs, compound category/product, sections/CMS, malformed/duplicate/overlong/controls, ignoredfields and malicious auth returns.
- [x] Run node --test tests/routes.test.js to capture RED.
- [x] Implement minimal explicit helper; no runtime schema/framework or dependency.
- [x] GREEN routes tests plus npm test/npm runcheck. Self-review exports/exactvalues, report and task-only diff.

### Task 2: App, public destinations and auth return

**Files:** Modify src/App.vue, src/components/AccountAccess.vue, LandingPage.vue, ProductCard.vue, CustomerAccount.vue, FeedbackManager.vue; minimalstylesonlyforanchor/buttonparity. Add tests/browser-routes.mjs; update only old browser selector expectations genuinely changed. Do not edit routes.js or private ContentManager/UsersManager owned byTask3.

**Consumes:** Task1 exports and normalized fields. Task3 exact component contract below, available during parallel integration.
**Produces:**
- App uses parsed route as source of selection; existing go accepts barefragment orcanonical '#...' and changes hash once. All new anchors use routeHash/targetHash.
- Category changes URL; directproduct uses existing getProduct. Stafforder list/detail opens through URL; close removes detail or returns safe remembered parent; direct arrivalclose remains onsite. Back/forward and reopen supported.
- Section/publication targets wait for publicsite+DOM; focus stable publication anchor or bounded missingtarget notice. No private data fetch for publiccontent.
- AccountAccess new prop returnTo:String default'' and event navigate(hash:String); allauthlinks/signup→activation/restart/reset preserve safeReturnTo. Parent passesroute.returnTo and handles navigate via go. Existing signupflow can require restart onreload but destination stays inURL; no new challenge persistence/PIIstorage.
- acceptSession and re-login prefer safe returnTo/requested private page, otherwise existing roledefault. Own-record destination with incompleteprofile moves to #account?returnTo=...; after profile activation restoresdestination. A validstaffdestination doesnot forcebuyerprofilecompletion.
- CRMinline legacylogin keeps currentdetailroute. Anonymousaccount/private-loginlinks preserveexactdestination. Wrongrole remainsdenied; no unauthorized component fetch.
- Parent private contracts: ContentManager :route-tab=route.tab :publication-id=route.publicationId @navigate=go; key byidentity+acceptedtab/publication so remountafteracceptedguard avoids duplicate dirty prompts. UsersManager :selected-user-id=route.userId @select-user=id=>go(routeHash({page:'admin-users',userId:id})). Emptyidreturnslist.
- CustomerAccount/FeedbackManager use helperforexistinglinks; requestcounters/privateguards remain intact. Do not change APIprotocols/order body serialization or mutating actions.

- [x] Add RED actual-browser tests for category/product reload,newtab/back-forward,stafforder open/close/directdeep-link,authreturn after email login/activation and missingprofile,externalreturnrejected,section/publication asyncarrival/missing target.
- [x] Run RED against preview Vite on rootassignedQAport; tests may mockAPI to control races but useactualVueDOM.
- [x] Implement routeintegration and anchors preservingfocus/guards; no newlibraries.
- [x] GREEN focusedbrowserroutes plus existing browseraccounts/feedback/API regression as appropriate; npm test/build/check. CoordinateTask3 props and report per-task diff.

### Task 3: Private menu/detail route bindings

**Files:** Modify src/components/ContentManager.vue andUsersManager.vue only; add tests/browser-private-routes.mjs. Mayconsume routes.js; noApp edits or shared tests writes.

**Consumes/produces:**
- ContentManager new props routeTab:String default'site', publicationId:String default''; existing props retained. New navigate event carries canonical hashString. Tabs/publication selection become links/emit navigation, not immediate local mutation. Initial accepted tab/publication loads byID independent oflistpagination. Parentkey remounts after route accepted; old onBeforeUnmount guards preventlate responses.
- UsersManager new prop selectedUserId:String default''; new select-user event carries opaqueID or'' forlist. Row selection emits; propwatch loadsgetUser byID, listbackhref#admin-users. Existing role/block mutations and optimisticversions preserved; do not expose roles throughsessions/ordinary pages.
- When standalonewithoutparent routebindings (existingtestharness), defaultprops stillrenderlist/site; tests can drive propupdates inactualAppQA.

- [x] RED browser tests direct #crm-content?tab=media/publications, directpublicationnotloadedpage, #admin-users?user=id afterlogin/reload, wrongrole denied, dirtydeclinekeepsoldURL/editor.
- [x] Capture RED againstactualApp; missingparentbindings may waitforTask2integration ratherthan countimporterrorsasfeatureRED.
- [x] Implementminimalprops/events/linkbindings, preservingcurrentvalidation/requestfencing/versionconflicts.
- [x] GREEN newprivate-route tests+existingrelevantbrowserchecks, build/check; self-review/report/taskdiff.
