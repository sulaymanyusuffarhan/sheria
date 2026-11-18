# TBC LSMS — evidence register and synthetic replay

**18 November 2026 · Baseline `d614bff085a568d88608e9131d84709660593c83`** · [Gap report](ega-v3-gap-report.md) · [Requirements matrix](ega-v3-requirements-matrix.md)

The evidence IDs below separate **source inspection**, **fresh synthetic engineering checks**, **historical records** and **missing institutional evidence**. Commands are for a throwaway development workspace, not a deployed instance. They use fictional records and isolated keys; never copy TBC data, credentials, sensitive access logs or recovery media into this repository. The local `.local/qa/` logs and screenshots were kept out of Git and may not persist; the results and replay procedures below are the durable record.

| ID | Evidence type | Principal result |
| --- | --- | --- |
| E01 | Source and document review | Implemented app controls and design/implementation differences identified; no formal requirements baseline. |
| E02 | Fresh code/DB checks and functional/security regressions | SQLite 59 pass, 3 PG-only skips; PostgreSQL 62 pass. |
| E03 | Fresh browser, accessibility and workflow checks | Three corrected replays passed; 23 automated accessibility states; sampled reflow and keyboard results. |
| E04 | Fresh defensive static/configuration review | Bandit 2 low matches, triaged; Django deployment check 2 warnings; no target network test. |
| E05 | Fresh package/advisory check | PyPI reported 20 distinct advisory IDs across 4 packages, 33 raw records. |
| E06 | Fresh tracked-file secret check | Four non-credential matches triaged; not full-history assurance. |
| E07 | Fresh isolated performance measurements | Two fixture sizes, one/four sessions; dashboard/search growth; not SLA testing. |
| E08 | Fresh isolated backup/restore and integrity drill | Same-release known-good synthetic restore to separate PostgreSQL cluster; wrong key refused. |
| E09 | Current GitHub read-only metadata + historical CI | Public individual repository, unprotected main, six historical checks, no recorded review for PR #4. |
| E10 | Institutional evidence not supplied / not executed | No authorised host, assessed target, approved SRS or sign-offs; no UAT/SAT or live operations performed. |

## E01 — Review of tracked design, code and configuration

| Area | Evidence pointers | What could be concluded |
| --- | --- | --- |
| Design and scope | [Proposal](../architecture/proposal.md), [workflows](../architecture/workflows.md), [model](../architecture/data-model.md), [README](../../README.md), [versioned API](../api/v1.yaml) | Development-level artefacts, not an approved BRD/SRS/SDD or an integration/hosting approval. Two application API reads are documented; no institutional IdP/integration is delivered. |
| Identity and scope | [Settings](../../lsms/settings.py), [middleware](../../lsms/middleware.py), [policy](../../identity/policy.py), [identity commands](../../identity/services.py), [matter selectors](../../matters/selectors.py) | Auth/session checks use local Argon2/password/TOTP in permitted modes, protected state-changing requests and scoped queries. ICT can create/edit/deactivate employee accounts and `create_employee` sets base Staff access; `access.decide` is separate for delegable roles. Production refuses local/demo, with no IdP route. |
| Audit and data | [Audit model and user/grant models](../../identity/models.py), [audit writer](../../identity/security.py), [audit views](../../identity/views.py), [casework models](../../casework/models.py) | App creates actor/area/time/target/correlation audit, plus revision histories. `AuditLog` appends via ORM and tech view excludes legal events. The DB itself is not demonstrated to enforce append-only audit or field-level encryption of personal data. App observes peer source address, not arbitrary forwarded headers; review ingress/IdP correlation separately. |
| Document protection | [Storage](../../casework/storage.py), [casework download](../../casework/views.py), [library access](../../library/services.py), [library downloads](../../library/views.py) | PDF, DOCX and XLSX validation, 5 MiB per file, 21 MiB request body, per-case limits, authenticated Fernet encryption and SHA-256 verification. Metadata, database and backup encryption are independent controls. No live ClamAV verification, approved rotation/escrow or operating custody programme. |
| Integrity and boundaries | [Specialist forms](../../specialists/forms.py), [hearing service](../../specialists/services.py), [hearing model](../../specialists/models.py), [PostgreSQL schema migration](../../specialists/migrations/0001_initial.py) | `CommandForm`'s shared reason permits 1,200 characters in both hearing disposition and rescheduling. `_hearing_disposition` assigns it without narrowing to the `CharField(max_length=800)` created by the migration. This was identified by comparing contract/service/schema, not by sending a failing request against any target. |
| Reports, docs, architecture | [Report implementation](../../workspace/reporting.py), [Phase 2 guide](../phase-2/guide.md), [Phase 3–4 guide](../phase-3-4/guide.md), [dependency notes](../phase-1/dependencies.md) | Fixed annual management cohorts and suppression are implemented; the phase docs and local operator setup are not a full release manual, controlled operator SOP, signed metric definitions or full dependency inventory. |

Review limits: these are selected security- and workflow-relevant code paths. There was no full line-by-line independent audit of the app, third-party code, assets, every integration or hosting/network control. No report of actual exposure follows from identifying a design gap.

## E02 — Regression and integrity checks

**Environment:** Linux x86-64, Python 3.11.2, Django 5.2.19, SQLite 3.40.1, bundled PostgreSQL 16.2 through `pgserver==0.1.4`. Both databases were freshly migrated and used synthetic data. No CI outcome was substituted for a current local run.

```sh
umask 077
mkdir -p .local/qa
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt pgserver==0.1.4
export LSMS_ENV=test LSMS_DEMO=0 LSMS_PREVIEW=0
.venv/bin/python -m pip check
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python tools/compile_i18n.py --check
.venv/bin/python manage.py test --noinput -v 2 > .local/qa/sqlite-test.log 2>&1
.venv/bin/python tools/postgres_smoke.py > .local/qa/postgres-test.log 2>&1
```

Use a clean shell and no inherited institutional `LSMS_DB_*` variables for SQLite. Django check reported zero issues in test mode; migration drift, Ruff lint/format, translation compile check and dependency consistency check passed in the assessed checkout.

| Suite | Expected | Observed |
| --- | --- | --- |
| SQLite | Functional/regression assertions pass; PG-specific `select_for_update` tests skipped. | 62 collected; **59 passed, 3 skipped**, 38.360 s test time. Skips: duplicate submission/triage, scoped access activation, reschedule/reminder worker race; nothing skipped due to unexpected failures. |
| PostgreSQL | Same tests including all three row-lock/race checks. | **62 passed**, no skips, 50.394 s test time. |

Coverage by reusable test files:

- [Identity](../../tests/test_identity.py) and [workspaces](../../tests/test_workspaces.py): local sign-in/cookie/session lifecycle, CSRF, attempt limiting, TOTP reuse/freshness, production local-auth refusal, enrolment, revocation/offboarding, append-only/redaction at the application, nine role paths, staff-safe projections, negative/direct scope, APIs and synthetic partitioning.
- [Casework](../../tests/test_casework.py), [storage](../../tests/test_casework_storage.py), [casework transactions](../../tests/test_casework_transactions.py): end-to-end review, idempotency, stale states, independent Head, versioning, exact evidence, PDF and execution; fail-closed missing/mocked scanner, bad uploads, constraints and rollback on notification failure. Scanner socket/transport verdict test uses doubles, not a live scanner.
- [Database integrity](../../tests/test_database.py) and [specialist transactions](../../tests/test_specialist_transactions.py): transactional changes and PostgreSQL races; projection migration only backfills already-published updates. Not a reconciliation of imported TBC records.
- [Specialists](../../tests/test_specialists.py) and [library, reporting and deadlines](../../tests/test_library_reporting.py): registry, settlement assurance, opinions, rights, compliance, metadata/bytes scope, messages, cohort suppression/reconciliation and export parity under synthetic policy.

No coverage percentage against business requirements can be inferred without the controlled SRS and a requirement-to-test map. Existing suites test defensive expectations and application rules; they are not a penetration test.

## E03 — Browser, language, accessibility and static assets

**Environment:** Node 22.22.3; Playwright Core 1.65.1, bundled Chromium 153.0.8010.0 and axe-core 4.14.1. Disposable synthetic SQLite on port 8010. Started with a fresh key and initial 114 matters (106 starter + Phase 2/3/4 fixtures); reminder invocation delivered **3**, then **0**. This checks deduplication, not a scheduler/job service.

| Replay | Result and limits |
| --- | --- |
| [`tools/browser_smoke.cjs`](../../tools/browser_smoke.cjs) | Passed legal charts/filter/search/detail, staff history/inbox/preferences/scoped pages, EN/SW switching, ICT directory/accounts, different approver, grant apply/revoke and offboarding. Skip-link and mobile keyboard menu tested. axe six states: sign-in, legal dashboard, staff dashboard, staff register, employee form, 390px staff dashboard. |
| [`tools/browser_phase2.cjs`](../../tools/browser_phase2.cjs) | Passed request through all 79 pre-clearance checks, independent Head decision, controlled issue, approved file and PDF downloads, separate execution, denials and notices. Six responsive/axe states incl. intake, checklist and frozen review. |
| [`tools/browser_phase34.cjs`](../../tools/browser_phase34.cjs) | Passed scoped litigation/calendar reschedule and keyboard, sealed library version and custody metadata, rights, compliance verification, frozen/issued opinion, management cohorts and scoped exports. Eleven axe states across these screens and desktop/mobile variants. |
| Extra size and language samples | Staff home, request list and blank intake measured viewport width = document width at 320 and 390 CSS px with JS enhancement active. The `.po` catalogue contains 1,099 translated entries, no fuzzy entries; this measures catalogue entries, not language accuracy or completeness of string extraction. |
| Asset preflight and test harness lesson | The first preview start omitted `--insecure`; preview `DEBUG=False` made the Django development server return **404** for CSS/JS. Phase 1/2 assertions failed and Phase 3–4 printed a pass without verifying CSS. Those initial browser results were discarded. With a fresh fixture and corrected **synthetic-only** invocation, `app.css`, `app.js`, and the bundled font returned 200 with expected content types and all three replays passed. Add explicit asset checks to a future acceptance harness. `collectstatic --noinput` separately populated local static files; no production static service was tested. |

Replay from the [Phase 3–4 setup](../phase-3-4/verification.md) or use the following with a new DB. Keep the server isolated and stop it at the end. `--insecure` is only for the disposable development server, **never** an institutional serving option.

```sh
umask 077
npm install --prefix .local/browser playwright-core@1.65.1 @sparticuz/chromium@153.0.0 axe-core@4.14.1
mkdir -p .local/qa
RUN=$(mktemp -d "$PWD/.local/qa/browser.XXXXXXXX")
export LSMS_ENV=development LSMS_DEMO=1 LSMS_PREVIEW=1 LSMS_PREVIEW_PORT=8010
export LSMS_DB_ENGINE=sqlite LSMS_DB_PATH="$RUN/fixture.sqlite3"
export LSMS_DOCUMENT_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
for command in migrate seed_demo seed_phase2_demo seed_phase34_demo; do
  .venv/bin/python manage.py "$command" || exit 1
done
.venv/bin/python manage.py deliver_legal_reminders --demo
.venv/bin/python manage.py deliver_legal_reminders --demo
.venv/bin/python manage.py runserver localhost:8010 --noreload --insecure
```

In a separate local shell, check `http://localhost:8010/static/lsms/app.css`, `app.js` and `fonts/inter-variable.woff2` respond 200 with intended content (not an error page). Then:

```sh
node tools/browser_smoke.cjs
node tools/browser_phase2.cjs
node tools/browser_phase34.cjs
```

No manual assistive-technology or disability participant sessions, target mobile hardware, low bandwidth/offline use, complete focus/error testing, accessible export review or WCAG conformance assessment. The checklist PDF finding in E08 only covers the sampled export.

## E04 — Static analysis and deployment configuration

- **Bandit 1.9.4** scanned ~8,611 lines across `casework`, `identity`, `library`, `lsms`, `matters`, `specialists` and `workspace` (excluding migrations and seed commands). Exit 1 for **0 high, 0 medium, 2 low** matches. Both are B406 on `xml.sax.saxutils.escape` imports ([casework PDF](../../casework/pdf.py) and [report PDF](../../workspace/reporting.py)): they escape generated output, not parse untrusted XML. Triaged as not XML parser findings at those calls. Not an exhaustive taint or framework policy analysis.
- **Django `check --deploy`**, with production settings in isolation, explicit hostname and ephemeral secret, resolved to PostgreSQL settings. It returned **W005** (HSTS does not include subdomains) and **W021** (preload not enabled). Consider only after approved domain and all HTTPS subdomains are verified. No production ingress, certificate or proxy was contacted. Configuration also allows remote DB connections with `sslmode=prefer` by default; require verified TLS on the remote path or an approved local alternative.
- **Not executed:** exploit development or reproduction, active attacks, automated offensive scanning, penetration testing or infrastructure vulnerability assessment. Escalate target assessment to the authorised institution under the agreed scope. This review provides defensive tests, source/configuration inspection and component advisories, not attack validation or an independent security report.

```sh
.venv/bin/python -m pip install bandit==1.9.4
.venv/bin/bandit -r casework identity library lsms matters specialists workspace \
  -x '*/migrations/*,*/management/commands/seed_*' -f json -o .local/qa/static-bandit.json --quiet
LSMS_ENV=production LSMS_DEMO=0 LSMS_PREVIEW=0 LSMS_DB_ENGINE=postgresql \
LSMS_ALLOWED_HOSTS=lsms.example.invalid \
LSMS_SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  .venv/bin/python manage.py check --deploy
```

## E05 — Dependency advisories

A separate venv installed only [`requirements.txt`](../../requirements.txt) with the normal bootstrap tools. **pip-audit 2.10.0**, using PyPI, evaluated 23 installed Python distributions, 0 skipped; exit 1. **33 raw advisory records**, **20 distinct IDs** after deduplicating repeated IDs within each package. `pip check` passed; it checks dependency consistency, not security. Current advisories and fix versions must be validated against the approved release environment; scanner hits do not establish reachability in LSMS.

| Distribution / version | Distinct IDs reported | Feed-listed fix versions (not upgrade recommendations) |
| --- | --- | --- |
| `cryptography 47.0.0` (direct runtime) | `PYSEC-2026-3552`, `PYSEC-2026-3553`, `PYSEC-2026-3554`, `GHSA-537c-gmf6-5ccf` | 48.0.1, 49.0.0, 50.0.0, depending on advisory. |
| `djangorestframework 3.16.1` (direct runtime) | `PYSEC-2026-3827`, `PYSEC-2026-3828`, `CVE-2026-114136`, `CVE-2026-114137`, `CVE-2026-114138` | 3.17.2, 3.18.2. |
| `pip 23.0.1` (bootstrap/build tooling) | `PYSEC-2023-228`, `PYSEC-2026-1795`, `PYSEC-2026-1796`, `PYSEC-2026-196`, `PYSEC-2026-2875`, `PYSEC-2026-2876`, `PYSEC-2026-3721` | 23.3, 25.3, 26.0, 26.1, 26.1.2, 26.2/26.2.0. |
| `setuptools 66.1.1` (bootstrap/build tooling) | `PYSEC-2025-49`, `PYSEC-2026-1918`, `PYSEC-2026-3447`, `GHSA-xmj3-9gf7-h52w` | 70.0.0, 78.1.1, 83.0.0, 84.1.0. |

No changes to pins were made. This covers the installed distributions at replay time, **not** a future resolved build, OS/native libraries (e.g. PostgreSQL and the scanner), browser/JavaScript/vendored assets, or institutional build environment. `collectstatic` also stages assets packaged with DRF; review which assets the release actually publishes and include them in the inventory/scans. Rebuild with compatible secured tools and approved hashes, discuss any unreachable paths or risk treatment with security owners, and run regression/advisory checks on the **exact** artifact. Do not use advisory absence alone to approve release.

```sh
.venv/bin/python -m pip install pip-audit==2.10.0
python -m venv .local/qa/runtime
.local/qa/runtime/bin/python -m pip install -r requirements.txt
.local/qa/runtime/bin/python -m pip check
.local/qa/runtime/bin/python -m pip freeze --all > .local/qa/runtime-requirements.txt
site=$(.local/qa/runtime/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
.venv/bin/pip-audit --vulnerability-service pypi --path "$site" --format json \
  --output .local/qa/dependencies.json --progress-spinner off --timeout 15
```

## E06 — Tracked-file secret review

`detect-secrets==1.5.0` default tracked-file scan (27 detectors) found four matches in three files: the TOTP alphabet constant in `identity/security.py`, a password field in `templates/registration/login.html`, and two fixed fictional password inputs in `tests/test_identity.py`. Triaged as non-credentials in context. No detected live credential in this snapshot; no conclusion for skipped/unsupported encodings, source history, binaries, CI variables/artifacts, forks or mirrors. The live repo remains public (E09). In approved custody, review complete history and artifacts privately; rotate any exposed credential and never paste matches into a change request.

```sh
.venv/bin/python -m pip install detect-secrets==1.5.0
.venv/bin/detect-secrets scan > .local/qa/tracked-secret-scan.json
```

## E07 — Synthetic latency and growth profile

[`tools/qa_postgres_replay.py`](../../tools/qa_postgres_replay.py) created PostgreSQL 16.2 on a new private Unix socket and passed requests through Django's test client to the application views/middleware and DB. It made no requests to other servers. Linux sandbox reported **2 CPUs, about 3.8 GiB total memory**, shared load. Small dataset: **114 matters, 111 staff projections**. Scaled dataset: **25,114 matters, 6,361 staff projections** (25,000 bulk-added demo matters, 6,250 additional projections belonging to one synthetic staff user; underlying documents, hearings and versioned histories remained small). Run outside a busy sandbox for less noisy comparisons.

For each endpoint/dataset, one warm-up query count was captured separately (not included in latency), then 40 serial requests and 40 requests spread over four concurrent synthetic authenticated sessions. Times include middleware, template/export generation and DB for the in-process request, not sign-in/network/browser rendering. p95 uses nearest rank; 0 non-200 responses out of 960 samples.

| Journey | SQL count (both datasets) | 114: serial p95 ms | 114: four sessions p95 ms | 25,114: serial p95 ms | 25,114: four sessions p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legal dashboard | 27 | 99.8 | 371.1 | 650.0 | 1,190.9 |
| Matter register text search | 17 | 84.3 | 270.7 | 356.4 | 736.2 |
| Staff request list | 13 | 37.2 | 126.7 | 52.2 | 114.5 |
| Court calendar (small hearing set) | 13 | 60.1 | 206.2 | 63.3 | 168.5 |
| Management dashboard | 19 | 45.7 | 198.0 | 178.5 | 315.3 |
| Management XLSX | 18 | 40.8 | 139.5 | 156.4 | 315.3 |

P95 growth justifies focused profiling of scoped aggregation/search and audit I/O. Lower measurements on unaffected routes are not claims of improvements. **No target SLA/availability or capacity verdict.** Confirm real data distribution, concurrency, live upload/scan, index health, long-running mixed load, infrastructure, transport and accepted thresholds before measuring a release candidate.

```sh
.venv/bin/python tools/qa_postgres_replay.py performance > .local/qa/performance.json
```

## E08 — Recovery and integrity drill

**Expected:** dump a freshly migrated and seeded PostgreSQL instance; after stopping/removing the source cluster, restore the logical dump on a distinct new local cluster. Compare model-row canonical hashes/counts and catalog constraints, verify migrated state, decrypt and verify every test document and recompute frozen-review digests. Confirm wrong key refusal and exercise authorised read/export after restore. Reuse only the generated fixture's document key, not an actual escrowed key. No real legal or employee records are used.

**Observed:** PostgreSQL 16.2, logical `pg_dump -Fc` of **338,940 bytes**, command time **0.080 s**; `pg_restore --exit-on-error` **0.479 s**, excluding fresh cluster setup/key retrieval/application verification. `migrate --check` completed. **62 model tables and 1,295 rows** (114 matters, 8 managed cases, 78 app audit rows among them) matched; **303 catalog constraints** matched. Initial comparison found a PostgreSQL deparser difference for eight `CHECK` expressions: one cast of a literal `varchar` array to `text[]` versus the equivalent `text` cast per literal. No row hashes differed. The final replay canonically compared that narrow equivalence, with all other constraint definitions and validation flags still compared.

All **9** encrypted documents decrypted and matched size/SHA-256; all **3** frozen review package digests recomputed; wrong key returned a validation error. A scoped demo Head could download a sealed version with correct content hash (HTTP 200) and regenerate a frozen checklist PDF (HTTP 200). The PDF parser did not find `/StructTreeRoot` in that checklist PDF, so tagged structure was not demonstrated for that format. The short dump/restore times are **not** RTO/RPO or proof of recovery at scale.

```sh
.venv/bin/python tools/qa_postgres_replay.py restore > .local/qa/restore.json
```

The tool creates synthetic fixtures itself, takes no DB/server target, and deletes its scratch clusters and dump. It intentionally uses disposable DB ownership and does not restore ACLs. Not tested: backup schedule or encrypted transport/media, off-site immutability, WAL/PITR, full key escrow/rotation, real migration/rollback, standby promotion, target application configuration/ACL restore, real-network access, business reconciliation of changes made after a restore point, or return to service in an approved DR environment.

## E09 — Repository and historical verification

Read-only GitHub checks on 18 November 2026 show: `isPrivate=false`, `isInOrganization=false`, default `main`, no licence reported by GitHub; `main` returned `protected=false`, effective branch rule list `[]` and rule set list `[]`. No releases listed. The shallow local clone had no tags; that is not a claim that there are no tags anywhere. No TBC-controlled canonical repository, approved hosting, authorisation group/audit export, attested rights or SCM restore/secondary site was supplied. There is no change to GitHub settings from this review.

[PR #4](https://github.com/sulaymanyusuffarhan/sheria/pull/4) merged `d614bff085a568d88608e9131d84709660593c83` at `2026-11-18T14:26:32Z`. Zero GitHub reviews recorded for that PR. Its [PR workflow](https://github.com/sulaymanyusuffarhan/sheria/actions/runs/44323955773) and [branch workflow](https://github.com/sulaymanyusuffarhan/sheria/actions/runs/44323923937) succeeded (Ubuntu SQLite, Windows SQLite, PostgreSQL 16 in each, six checks total). [Workflow](../../.github/workflows/phase1.yml) verifies code/test/migrations, does not enforce a build/deployment or security approval. Absence in GitHub is not proof that no offline review occurred; no matching sign-off was supplied. The earlier populated Phase 2-to-Phase 3–4 SQLite upgrade is documented in [historical verification](../phase-3-4/verification.md); it was not repeated as a full release rollback.

```sh
gh repo view sulaymanyusuffarhan/sheria --json isPrivate,isInOrganization,defaultBranchRef,licenseInfo
gh api repos/sulaymanyusuffarhan/sheria/branches/main --jq '{name,protected}'
gh api repos/sulaymanyusuffarhan/sheria/rules/branches/main --jq '[.[]|{type}]'
gh api repos/sulaymanyusuffarhan/sheria/rulesets --jq '[.[]|{name,enforcement}]'
gh release list --repo sulaymanyusuffarhan/sheria --limit 5
gh pr view 4 --repo sulaymanyusuffarhan/sheria --json state,mergeCommit,mergedAt,reviews,statusCheckRollup
```

## E10 — Institutional evidence needed, not supplied

| Evidence owner | Needed to change the status of open rows |
| --- | --- |
| DLS, HR, Records, Management | Signed BRD/SRS, TBC/DLS/CRF/001/2026 controlled master and approved translations, authority/role/clearance matrix and legal sources, calendar/reporting definitions, reconciled records requirements; trained representatives' signed UAT Report. |
| ICT, Security, DBA and QA | Approved EA, SDD, network/hosts/IdP/scanner integration, protected logging and DB role design, support and environment separation; security assessment and remediation report; exact-artifact SAT and rollback/DR results against approved RPO/RTO; performance, inclusion and ops evidence. |
| DPO and Records | Approved lawful handling and retention/disposition/holds, privacy assessment and breach/request procedures, key and backup custody, access reviews and lawful transfer arrangement. |
| Steering, Procurement and e-GA liaison | Signed feasibility, acquisition/hosting applicability and decisions, source ownership and approved SCM arrangements, applicable licensing/agreements, OLA/SLA, named accountable operators, change and release approvals, any duly authorised exception, institutional/regulatory assessment and sign-offs under the applicable process. |

**Not run or witnessed:** institutional UAT or SAT; signed architecture/requirements or change reviews; live malware or identity integration; target security evaluation, operational monitoring or approved release/rollback; real-data migration/parallel reconciliation. None has an assumed pass. No production deployment, hosting or repo-policy change was executed.

### Recorded local-run digests (optional provenance)

These identify the original local files if present. A valid replay normally produces different durations, timestamps, IDs or data; identical hashes are not required for the replay to pass.

| Run output in `.local/qa/` | SHA-256 |
| --- | --- |
| `sqlite-test.log` | `a1c105342dbf659b1c5f86d72b4aa9c1a68d87897e96f1fd893c4dc179dbcbd0` |
| `postgres-test.log` | `69142b41b18aa347491a89498fa5081334500fe1340592822254071e113fa615` |
| `dependencies.json` | `a1d7274f8ea99a6e0fb2c28aa524353e9651c5d7c4b075a8fc104c95da75e378` |
| `performance.json` | `f0f53397681a8848d4c972b996468f72dea0ea033483a8f90479274d3dccc999` |
| `restore.json` | `fe6b159a571ac0e349b5ee85fa56a11cffd31b6965cfa65ea5b2ca7ad67d065c` |
