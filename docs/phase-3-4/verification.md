# Phase 3–4 verification

Verified on 18 November 2026 using synthetic data. The checkout remains a development/UAT candidate, not a production authorisation.

## Results

| Check | Result |
| --- | --- |
| Django system check, migration drift, Ruff lint and format, compiled Kiswahili catalogue, `pip check` | Passed. |
| SQLite 3.40.1 | 59 passed, 3 skipped (row-lock races require PostgreSQL). |
| PostgreSQL 16.2 | All 62 tests passed, including competing hearing reschedules and reminder delivery. |
| Phase 2 upgrade | Migrated a populated database from `e99274c`; existing case references/revisions, frozen review digests and file checksums were unchanged. |
| Synthetic setup | Seeded Phase 3–4 twice with no duplicates. The resulting preview includes 114 matters, 8 managed cases, 4 versioned repository items, an adjourned and a scheduled hearing, an issued opinion, 2 licence terms, and a compliance obligation. Two successive reminder runs delivered 3 and 0 notices. |
| Browser | Chromium 153.0.8010.0 passed the Phase 1, Phase 2 and Phase 3–4 scripts. The specialist run covered six role sessions, keyboard day selection, EN/SW, PDF/Excel downloads, scoped custody and management views, and 390/1440 px layouts. Axe 4.14.1 checks targeted WCAG 2.0/2.1 A/AA. Automated tests are not an accessibility certification. |
| Arena preview | Synthetic-only proxy host, HTTPS-forwarded login and CSRF, secure cookies, CSS, register/calendar access and officer denial on management exports passed. |

The new tests are in `tests/test_specialists.py`, `tests/test_library_reporting.py` and `tests/test_specialist_transactions.py`. They check:

- Internal registration, idempotency, scoped assignment, denied direct access, CSRF and stale revisions.
- Litigation: calendar scoping/history, reschedule and disposition, separate strategy scope, documentary outcomes, independent fresh authentication for settlements and gated closure.
- Opinions: returned Senior review, exact version binding, overlap of Senior and Head roles, approval without issue, named recipient vs department viewer, and reclassification blocking a previously eligible download.
- Rights: exact provenance from the shared repository, renewal date order, infringement records and links; clearance alone cannot supply an executed contract link, and loss of target scope hides it.
- Compliance: independently verified effective source, evidence and gap plans, return/resubmission, corrective verification and supersession without lost history.
- Repository: unsealed metadata vs byte access, sealed item isolation, direct document URL denial, quarantine/recheck, integrity failure, hold/lift/archive and current role, assignment and clearance checks.
- Reporting: eligible period cohorts, separate real/demo partitions, confidential/restricted exclusion, small and complementary suppression, distinct matter counts, output parity and role revocation. Reminders: EAT rollover, dedupe, escalation, adjournment/cancellation and inbox revocation checks.

## Replay

Follow the [README](../../README.md) for a fresh, **disposable synthetic** database and a dedicated document key. Restore a clean database before rerunning the specialist browser script: it reschedules a hearing, uploads a new version, places a hold and resolves a synthetic corrective action.

```sh
npm install --prefix .local/browser playwright-core@1.65.1 @sparticuz/chromium@153.0.0 axe-core@4.14.1
# Start the seeded demo on localhost:8010 in a separate terminal.
python manage.py runserver localhost:8010
# Then, with Node.js:
LSMS_SCREENSHOTS=1 node tools/browser_smoke.cjs
LSMS_SCREENSHOTS=1 node tools/browser_phase2.cjs
LSMS_SCREENSHOTS=1 node tools/browser_phase34.cjs
```

`LSMS_BROWSER_EXECUTABLE` can select an installed Chromium. Optional synthetic screenshots are written under `.local/`, not Git. The specialist script checks the litigation docket, desktop/mobile calendar, sealed version, records metadata, rights portfolio, compliance review, frozen/issued opinion, and desktop/mobile management dashboard.

## Before institutional use

- Approve the authority matrix, classification/clearance provisioning, report cohorts and thresholds, official reference sources, translations, deadlines and escalation rules. The software records attestations; it does not create court, settlement, broadcast or regulator authority. The synthetic sources and demo Head decisions are not institutional decisions.
- Integrate the approved identity provider and fresh authentication, live scanner, private backup/key custody and recovery, audit protections, staged rollout and retention/disposition procedure. Without a verified scanner, non-demo files remain quarantined; the automated suite uses mocked scanner outcomes, not a live ClamAV instance.
- Approve and operate the in-app reminder schedule with monitoring and operational retry. No external channels, integration feeds or holiday/business-day rules are enabled. No deletion or automatic disposal is implemented. The repository uses the existing encrypted case store and upload limits; capacity and recovery must be validated for real volumes.
- Reconcile any imported historical records and the controlled Phase 2 checklist. Complete DLS/ICT acceptance, manual accessibility review, privacy/security assessment and the e-GA evidence trail before processing any real records.
