# Phase 1 engineering verification

**Date:** 18 November 2026 (EAT) · **Status:** Engineering checks passed for the Phase 1 foundation. No UAT, System Acceptance, production authorisation or e-GA assessment is implied. Phase 2 was not started.

## Scope delivered

- Versioned schema, seed migrations for nine roles, their permissions and bilingual categories; department/directorate directory and active memberships. Runtime role policy evaluates active grants and their scope before lists, details, dates, charts, searches, counts and APIs.
- One identity system. Non-demo local login uses Argon2id, DB rate limits, and encrypted TOTP with replay prevention for elevated roles. Password changes, offboarding and role-activation/revocation invalidate existing sessions as appropriate. Preview sign-in is a separate, POST-only synthetic role picker, not real authentication enrolment.
- ICT can provision/edit/deactivate employee accounts and maintain the directory. A scoped role request requires a separate authorised Head of Legal Services decision before ICT can activate it. Approval here is **identity authorisation**, never clearance of a contract or other legal matter. ICT cannot grant itself legal privileges or read the legal register.
- Staff see their own published projections. Department heads only see designated internal requests in their authorised department. A public status is independent of internal matter state. Legal officers see active assignments, senior officers see their scoped departments, and the Head sees the institutional legal scope; confidential/restricted matters additionally need matching, current named clearance. Management sees an aggregate with small-cell suppression, Records sees scoped metadata, and Audit sees redacted event history. The legal register and staff tracking are read-only.
- Status, category and date charts, deadline queues, search and filters use database records; empty scopes show zero or a clear empty message. The profile, language switch, notification inbox and read-state operations are available across workspaces. [API v1](../api/v1.yaml) documents two read-only same-origin endpoints.
- The repeatable fixture generates **106 synthetic matters**, published requests and timelines, **14 synthetic identities** (nine selectable roles), deadlines, and in-app notifications. No passwords, true institutional records, actual legal advice, obligations or court outcomes are included.

## Executed verification

| Gate | Evidence | Result |
| --- | --- | --- |
| Identity and offboarding (T01) | `tests/test_identity.py`: local sign-in and CLI enrolment; password verification/throttling, TOTP replay, secure session rotation, idle and absolute expiry, logout, CSRF and invalid-origin handling. Disablement revokes elevated grants, assignments and named clearances, cancels pending access and invalidates the old session. | Pass on both engines. Institutional IdP is out of scope and not connected. |
| Scoped access (T02, T10) | `tests/test_workspaces.py`: all nine role landings and denial routes, forged filter and direct-ID denial, expired grants, assignment and named-clearance checks, no private fields in staff HTML/API, dashboard/series/count agreement, empty states and management/records separation. | Pass on both engines. |
| Real writes and audit (T02, T15) | `tests/test_identity.py` and `tests/test_database.py`: valid/invalid directory and employee forms, request → independent approve/reject → apply → revoke; duplicate, self-request, stale decision and bypass prevention; audit actor/time/source and session correlation with application-layer update/delete denial; per-user notifications/preferences. | Pass. PostgreSQL also verified two concurrent activation attempts commit only one grant and one activation audit event. |
| Schema and portability (T12) | Python 3.11.2 on Linux x86-64. Fresh migration, repeat demo seed, constraint tests and upgrade from legacy already-published statuses. `python manage.py check` and `makemigrations --check --dry-run` clean. | SQLite **3.40.1**: **28 passed**, 1 PostgreSQL row-lock test skipped. PostgreSQL **16.2**: **29 passed** against a temporary disposable cluster. |
| Browser and language (T11) | Playwright 1.65.1, Chromium 153.0.8010.0, axe-core 4.14.1. Desktop 1440×980 and mobile 390×844. Clicked through legal filters/search/detail, staff tracking/deadlines/notifications/preferences and ICT create/approve/apply/revoke/offboard using separate sessions. Verified EN/SW, focusable skip link, keyboard menu and viewport reflow. | Pass. Six views checked for applicable axe WCAG 2.x A/AA rules, no reported violations on that run. This is not an accessibility conformance assessment. |
| Preview boundary | Tested the preview's expected HTTPS host and origin headers, CSRF POST, secure cookie flags and scoped access. Preview uses only a separate synthetic SQLite database. | Pass at the proxy-header boundary; see browser privacy note below. |

Tests, lint and catalogue consistency commands are in the [README](../../README.md). CI definitions for SQLite (Linux/Windows) and PostgreSQL are provided; the runs above are **local**. Windows, MariaDB and non-Chromium browsers have not been verified in this session.

### Replay the browser regression (optional)

Use a fresh, disposable database: replay changes directory, users, notifications and account preferences. On Linux x86-64 with Node.js 22.17+:

```sh
npm install --prefix .local/browser --no-audit --no-fund \
  playwright-core@1.65.1 @sparticuz/chromium@153.0.0 axe-core@4.14.1
export LSMS_ENV=development LSMS_DEMO=1 LSMS_PREVIEW=0
export LSMS_DB_PATH=".local/browser-test-$(date +%s).sqlite3"
python manage.py migrate --noinput
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8010
# In another terminal at the repository root:
node tools/browser_smoke.cjs
```

`LSMS_SCREENSHOTS=1` saves screenshots locally under `.local/browser-artifacts/`. The smoke script refuses non-loopback hosts. In an embedded preview, if browser privacy controls block the sandbox cookie, open that same preview in a new tab; do not disable browser protections globally.

## Before real use or later phases

- TBC ICT, DLS, HR and Records must validate the real organisation/portfolio boundaries, grant owners, bilingual terminology and branding. Dev samples and the built-in emblem are not approved institutional assets. Verify the Head's access-governance delegation separately from legal powers and confirm the exact staff publishing rule.
- Production login remains disabled. Approve and integrate institutional IdP/MFA, deprovisioning, recovery and break-glass processes. Confirm approved hosting/DNS, secrets/KMS, encrypted storage/backup, restoration, database roles, audit retention and source-network attribution. Audit append-only enforcement here is **application-level**; use a separate runtime DB role with insert/select-only audit rights and protected external retention before real data. Source address is the direct peer, not a client-supplied forwarding header. Confirm reverse-proxy rate limits for shared networks.
- No file storage or document access endpoints are exposed. The classification/clearance policy is the foundation; uploads, quarantine and scan, release and versioning, record holds, contract checklist `TBC/DLS/CRF/001/2026`, legal workflow, and integrations are unimplemented. No legal decisions can be issued from this phase. Run independent security testing, formal UAT/SAT, accessibility evaluation and authorised deployment assessment before any production use.

Open institutional controls remain tracked in the [architecture proposal](../architecture/proposal.md) and [e-GA mapping](../architecture/ega-v3-mapping.md).
