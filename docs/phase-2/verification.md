# Phase 2 — engineering verification and acceptance boundaries

**Status:** implemented for a synthetic development demonstration. Not institutional acceptance, e-GA approval, legal advice, or authorisation to process real data. See the [workflow guide](guide.md).

## Checklist source and fidelity

The working source is the inventory of `TBC/DLS/CRF/001/2026` in [workflow design §3](../architecture/workflows.md#3-tbc-official-contract-review-form). There was no controlled PDF, Word document or other master in the checkout or workspace attachments. **Preserved:** the form number, DLS ownership, Internal Use Only label, section order, identity and decision fields, and counts below. **Not verified:** verbatim item wording, controlled form typography, and the approved Kiswahili translation. The UI and PDF explicitly label the template as draft. DLS should provide the master before adoption; reconcile it under a new template revision rather than rewriting frozen decisions.

| Part | Title | Captured fields |
| --- | --- | --- |
| A | Contract Identification Details | 12 identification fields pinned to the reviewed version |
| I | Preliminary Review | 8 checks |
| II | Contracting Parties Review | 7 checks |
| III | Legal and Regulatory Compliance Review | 8 checks |
| IV | Contract Terms Review | 7 checks |
| V | Financial Review | 8 checks |
| VI | Intellectual Property and Content Review | 8 checks |
| VII | Risk Allocation Review | 8 checks |
| VIII | Digital, ICT and Data Protection Review | 8 checks |
| IX | Termination and Dispute Management | 10 checks |
| X | Counterparty Due Diligence | 7 checks |
| XI | Risk Classification | Four classes; category, description, likelihood, impact, mitigation and owner for each row |
| XII | Approval and Legal Clearance | Four recommendation labels; authenticated officer and separate Head, time, reason, outcome and conditions |
| XIII | Post-Execution Requirements | 6 checks; separate recorded signing, custody, monitoring and finalisation |

Parts I–X contain **79** checks; XIII contains **6** more. Items have stable IDs and explicit Yes/No/N/A versus unanswered. Checks can carry reasons and up to three case-scoped evidence versions. The PDF uses the frozen review (and an explicitly separate execution section if applicable); it is an internal export and not a signed counterpart. The dossier has section-by-section saves and revision conflicts, not autosave or offline editing.

## Verified on 2026-11-18

| Evidence | Local result |
| --- | --- |
| Django checks, migration drift, Ruff lint/format, dependency and compiled catalogue checks | Passed. Added 284 draft Phase 2 Kiswahili UI entries; checklist prompt text is not treated as an approved translation. |
| SQLite 3.40.1 | 48 discovered; **46 passed**, two PostgreSQL lock tests skipped. |
| Disposable PostgreSQL 16.2 | **48/48 passed**, including competing submissions/triage and single access activation. |
| Chromium 153.0.8010.0, four independent sessions | Staff creation/upload → intake question → response verification → assignment → all 79 checks and risk → frozen review and PDF → simulated Head decision → controlled issue → staff download/inbox. Checked that staff could not see private data or proposed files before issue and that clearance did not record execution. |
| Chromium responsive and axe-core 4.14.1 | No WCAG 2.0/2.1 A/AA violations reported on six Phase 2 pages scanned; width checked at 390px mobile and 1440px desktop. Intake EN/SW switch and existing Phase 1 browser replay also passed. This is not accessibility certification. |

The HTTP suite uses synthetic actors with actual TOTP verification and a used-code replay check. It also covers ineligible/revoked roles, cross-case and quarantined evidence, duplicate/stale mutations, refusal to self-approve or skip stages, conditional/rejected/superseded outcomes, strategic evidence, frozen PDF content, post-execution and post-lock controls, document encryption and wrong keys, scanner adapter responses, CSRF and rollback. Browser confirmation in demo is intentionally not real MFA. Scanner responses and failure cases were exercised, not a live ClamAV deployment.

### Replay

Follow [README setup](../../README.md#try-the-isolated-demonstration) for a **new** disposable SQLite DB, distinct from the preview or any pilot. For browser runs use `seed_demo` on its own to recreate the baseline. With the demo environment and document key loaded:

```sh
# Start this test-only server in a separate terminal.
python manage.py runserver localhost:8010 --noreload

# Install local browser tools (Linux sandbox bundle) and replay.
npm install --prefix .local/browser --no-audit --no-fund \
  playwright-core@1.65.1 @sparticuz/chromium@153.0.0 axe-core@4.14.1
node tools/browser_smoke.cjs
LSMS_SCREENSHOTS=1 node tools/browser_phase2.cjs
```

You can point `LSMS_BROWSER_EXECUTABLE` to another local Chromium binary. Browser test scripts accept only a local `localhost` base and a synthetic login. Screenshots are written to ignored `.local/` paths. The ordinary `manage.py test` suite and Linux/Windows SQLite plus PostgreSQL jobs in the existing CI workflow include the new tests. Local verification is not a claim that remote jobs have run for this branch.

## Open gates

- Supply the DLS controlled checklist plus approved languages, routing/delegations, intake sufficiency, numbering, escalation/strategic authority, any external vetting, signatory authority, and final execution/retention policy. The stage rules are an explicit draft baseline, not encoded statutory thresholds or approval to sign. The preview uses a provisional emblem.
- Before a pilot, validate live malware scanning and quarantine operations, storage and backup/key recovery, evidence/archival custody, retention and legal holds, institutional identity and SSO/MFA, roles, infrastructure and incident handling. Close is nondestructive; there is no disposal action or e-signature.
- The delivered scope is request intake and review, contract/MoU specialist review, private files, release and in-app events. Persistent pre-submission drafts, external message channels, scheduled/working-day reminders, general specialist registers and advanced retention/custody are not included. Phase 1 rows are not silently migrated to approval history.
- Run approved functional UAT, SAT, accessibility, security, regional performance, recovery and e-GA assessment before institutional rollout.

## Added runtime dependencies

ReportLab 5.1.0 (BSD, PDF generation), pypdf 7.1.1 (BSD-3-Clause, PDF structure checks and test extraction) and defusedxml 0.7.1 (PSFL, defensive OOXML XML parsing). The ClamAV daemon is an operator-managed integration, not a bundled dependency. These checks do not substitute for dependency review and vulnerability monitoring.
