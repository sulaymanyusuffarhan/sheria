# TBC Legal Services Management System — architecture proposal

**Version:** 0.1 · **Date:** 18 November 2026 (EAT) · **Status:** For review; not an approved baseline

**Proposed business owner:** Directorate of Legal Services (DLS) · **Proposed system custodian:** TBC ICT

**Review decision:** Endorse or amend this design as the basis for requirements workshops and formal sign-off. No application code, database, infrastructure or integrations have been created. This proposal is not deployment authorisation, a legal opinion, an e-GA assessment or certification.

## 0. Repository inspection

Inspected branch `arena/241e9456-sheria` at `cce5cdc` (`Initial commit`), including the tracked tree and working directory. Before this review the branch was clean and its only tracked file was `README.md`, containing `# sheria`. There were no source files, dependencies, build tools, UI assets, schema, migrations, tests or deployment configuration to reuse. **Treat this as a greenfield application; reuse the Git repository and history, not a presumed application stack.** This does not establish that TBC lacks existing systems or paper registers. Inventory those separately before choosing what to build, reuse or migrate.

## 1. System architecture

**Recommendation: one modular application, two authenticated workspaces, one transactional record of legal activity.** Use a modular monolith so submission, assignment, decision and audit can commit together. Multiple web/worker replicas can be added independently if demand warrants; do not start with microservices, a separate search cluster or a separate database per portal.

```text
TBC browsers (HQ and approved regional access)
      │ HTTPS on approved network / remote-access path
      ▼
TBC-managed DNS, reverse proxy, certificates, request limits
      │
      ▼
LSMS web application ─────────────── Approved institutional identity provider
  /portal   Staff    /admin   DLS, management and scoped oversight
  /api/v1   Authorised API, shared policies and domain services
      │
      ├─ Transactional DB: records, workflows, audit, outbox, jobs
      ├─ Private quarantine and document store: encrypted versioned objects
      └─ Private workers: scan/preview, export, reminders, approved adapters
                                      │
                           Approved mail/SMS and GovESB, when onboarded

Separate restricted destinations: backup/DR, operational alerting, audit archive.
Database, storage, workers and operational interfaces are not browser-accessible.
```

| Layer | Proposed choice and rationale |
| --- | --- |
| Web and API | Supported Django LTS and Python, Django templates and progressively enhanced responsive components; Django REST Framework with a versioned OpenAPI contract. One code path for form and API commands. Page-level access and hidden controls are not security boundaries. |
| Database | PostgreSQL as the proposed primary; ORM migrations and portable constraints for the core. Validate a second relational engine (candidate: MariaDB LTS with transactional tables), not just SQLite tests. Isolate full-text search and any engine-specific tuning behind adapters; permission-aware metadata search is the portable baseline. |
| File and background work | Government-hosted private object storage on an ICT-approved interface, institutional key/secret management, isolated scanner and rendering workers. A DB-backed outbox and durable leased job queue start simple; workers are separate processes from HTTP. |
| Operations | Approved Linux VMs/containers behind the institutional proxy; reproducible builds; separate development, test/staging and production identity, credentials, data, storage and release controls. Test supported alternate OS paths, including an agreed Windows configuration; containers alone are not proof of OS or database portability. |
| System boundary | TBC staff and authorised operational users only. No public intake, public case search, external advocate login, generative legal advice or native contracting/signature service in the baseline. Integration adapters are off until approved. |

**Module boundaries.** Identity and organisation own user lifecycle, role grants and reference codes. Intake owns submissions and staff-safe correspondence. Matters and workflow own assignments, state, decisions and escalation. Contracts, litigation, opinions, IP/broadcasting rights and compliance own their domain records. Documents own files, versioning, release and disposal. Calendar, notifications, reporting and audit consume authorised records/events; none can approve a matter. Modules use explicit service interfaces; no direct editing of workflow state from a generic admin console.

**API and consistency.** Start with same-origin pages and authenticated, paginated `/api/v1` resources. Separate staff response models from legal views. Use dedicated commands for submit, request information, assign, recommend, decide, return and release. Validate inputs and scope server-side, including filter/sort fields. Each state change checks an expected revision, resulting in a reload/conflict response if stale. Submissions and retryable commands carry an idempotency key; reuse of a key with a different payload is rejected. Commit workflow event, related state, audit and outbox together; workers deliver/retry without recreating the command. Publish interface schemas, a redacted error catalogue, and adapter-specific data contracts. No browser direct-to-storage or direct access to another module's tables.

**Architecture alternatives.** Prefer existing government or TBC services for capabilities that meet scope and confidentiality requirements. Compare reuse/extension of an approved government platform, in-house build and permitted procurement against checklist fidelity, access isolation, local support skills, integration, lifecycle cost and exit/portability risk. This modular stack is the custom-build candidate, not an acquisition decision. Keep licences and institutional ownership traceable. Size web, DB, storage, scanner capacity and recovery topology after expected staff concurrency, attachment sizes, historical volumes and regional bandwidth are known.

## 2. Roles and permission matrix

This matrix proposes **maximum baseline capabilities**, not blanket access. DLS = Directorate of Legal Services. A dash means no grant by that role. An ordinary employee function, if separately granted, is limited to the person's own employee account and records.

| Role | Intake and tracking | Legal work and workflow | Final decision and issue | Documents and reports | Technical, records or audit |
| --- | --- | --- | --- | --- | --- |
| System Administrator — ICT | — | — | — | No legal content, search or reports | Account provisioning/deactivation, technical settings, availability and backup operations; redacted technical/security events only. No self-grant or approval of legal privileges. |
| Staff / Requesting Officer | Submit; view own staff timeline; respond to own information requests | — | — | Upload to own request; download explicitly released versions for own audience | Own profile and notification preferences |
| Head of Department | Same as staff, plus authorised departmental summaries and named submissions | Departmental business endorsement only if delegated; not legal review | — | Own releases and explicitly authorised departmental releases; departmental aggregates only if separately granted | No automatic access by department membership |
| Legal Officer | Intake only if given a triage grant; otherwise assigned matters | Draft, checklist, note, update authorised case/task, recommend within assignment | No final approval; issue approved version only with explicit issuer permission | Scoped preview/download and upload; restricted strategy/evidence has its own grant | Own work queue |
| Senior Legal Officer | Triage in assigned queue; verify, accept/return/reject where delegated | Assign and supervise within portfolio; senior review and recommendations | No final approval by senior role alone; approved issue only when authorised | Scoped documents and team workload; exports separately granted | No IAM or policy override |
| Head of Legal Services | DLS leadership scope; restricted files still require clearance | Supervises, approves business access grants and policy changes under dual control | Decides within mandate; cannot finally approve their own submission or authored recommendation; may authorise release | Scoped oversight, approved disclosures and reports | Cannot alter audit history or bypass workflow |
| Management / Director General | — | Sees explicitly released executive briefs; no general case access | No legal clearance; external executive disposition, if required, is recorded as separate evidence | Approved aggregates and scoped exports; no drill-through by default | — |
| Internal Auditor | — | — | — | No legal content by audit role | Read-only audit events/evidence for an authorised audit mandate; redacted by default, no edit or delete. Audit export needs explicit approval. |
| Records Officer | — | — | — | Assigned records/metadata queue; file bytes and privileged records need separate access | Classify, apply retention, archive and propose disposition within mandate; no unilateral purge. |

- Enforce **role action + active scope + record relationship + classification/purpose + current workflow stage**. Scopes include own request, authorised department/office, assigned matter or queue, supervised portfolio and time-bound record grant. Evaluate a complete grant in context; permissions from one scope cannot be combined with another scope to widen access. Default deny; explicit restrictions and conflicts win. A TBC-wide role does not confer access to sealed matters, litigation strategy, sensitive HR disputes or confidential opinions by itself.
- Separate `request.track`, `intake.verify`, `assignment.manage`, `matter.recommend`, `approval.decide`, `matter.issue`, `case.strategy.read`, `document.preview`, `document.download`, `document.release`, `report.export`, `audit.read` and `identity.provision`. No assignment grants file download or approval unless explicitly allowed by role and record policy. Apply the same controls to list counts, autocomplete, filters, attachments, status timelines, notification payloads and exports; omit inaccessible rows and identifiers.
- TBC ICT executes account changes; role owner approves the legal scope and a different custodian applies privileged grants. Privileged technical identities cannot also hold legal approval roles. Formal acting Head delegation is named, scoped, time-bound and audited, with conflict checks against the underlying person. Confidential staff/department views get only approved status, dates, public-facing messages and individually released files. Revocation takes effect on the next authorised operation and for queued exports/downloads.

## 3. Database and records

See the [logical schema, ER diagrams and integrity rules](data-model.md). A legal matter anchors classification and ownership. An optional staff request and an optional specialist record share the anchor, with independent but coordinated lifecycle lanes. Append-only events and decision snapshots preserve who did what and which version they reviewed. Files have immutable byte versions and explicit publication records. Departments and assignments are historical, so a later transfer does not silently broaden access or rewrite old reporting.

## 4. Front-office and administrative sitemap

Proposed host `https://legal.tbc.go.tz` is subject to ICT/domain, certificate and hosting approval. `/` sends an authenticated user to an allowed workspace, or to `/login`. Approved roles see a workspace switcher. Login/logout and account recovery share one identity service, not two password systems. All substantive pages require authentication.

| Surface | Proposed routes and content |
| --- | --- |
| Staff home | `/portal` — my total, awaiting legal review, processing, needs information, completed and rejected; recent submissions and staff-visible deadlines. Authorised heads may switch to their department's permitted projection, never its internal legal notes. |
| Staff intake | `/portal/requests/new` for general intake, with `contract`, `opinion`, `dispute` and `rights` subroutes. One form engine with category-specific fields, resumable drafts, department/office from verified memberships, priority, due-date request, attachments and declaration. The EAT-year request reference is assigned on successful submission (e.g. `TBC/DLS/2026/0001`). |
| Staff follow-up | `/portal/requests` → `/{id}` for tracking, permitted correspondence, attachments and additional information; `/portal/documents` for explicitly released versions; `/portal/notifications`; `/portal/profile` for permitted settings, not HR identity editing. |
| Legal home | `/admin` — DLS dashboard or allowed role landing. Legal cards: total matters, new, unassigned, under review, pending approval, completed, active litigation, contracts awaiting review, high risk, overdue assignments, and upcoming hearings/contract expiries. Charts by month, directorate, category, officer workload, contract risk, case status and completed/pending. |
| DLS operations | `/admin/requests` and `/{id}` (intake, assignments, notes, history and releases); `/admin/contracts` and `/{id}` (register, versions, checklist, risk, clearance, conditions, amendments, execution and archive); `/admin/reviews` (scoped decision queue). |
| Specialist registers | `/admin/litigation` (cases, hearings, evidence, restricted strategy, outcomes and settlements); `/admin/opinions` (research, versions, supervisory review, issue and register); `/admin/rights` (content, licences, restrictions, expiry and infringement); `/admin/compliance` (verified obligations, evidence, gaps and corrective actions). Each has an authorised detail view. |
| Shared operations | `/admin/documents` (search, tags, versions, authorised preview/download); `/admin/calendar`, `/admin/tasks`, `/admin/notifications`; `/admin/records` (classification, holds, retention and transfer). |
| Oversight and support | `/admin/management` and `/admin/reports` (authorised dashboards, executive briefs and exports); `/admin/audit` (authorised event review); `/admin/governance` (business workflows, templates and legal-reference approvals); `/admin/ict` (accounts, role-grant execution, technical settings, job/health status and change requests). No unrestricted framework administration in production. |

**Usability.** Use professional navy/white and approved TBC brand assets only. Provide English and Kiswahili navigation, validation, status labels and notification templates; DLS verifies the legal glossary and any translations without replacing original records. Use day-first display (e.g. `18/11/2026`), configurable EAT display default (`Africa/Dar_es_Salaam`), UTC event timestamps, and local date-only storage for date-only deadlines. Propose WCAG 2.2 AA as an acceptance target, subject to applicable government requirements: labelled forms, keyboard/focus support, meaningful status text, accessible chart tables, responsive layouts and server-side pagination for regional connections. No sensitive offline storage in the baseline.

**Reporting semantics.** Derive every card and graph from scoped records, not fixed values. For example, unassigned means an accepted, non-terminal matter without an active primary assignee; high risk means the latest applicable recorded human risk assessment, with strategic risk displayed separately. Filter legal dashboards/reports by period, department, officer and category, with explicit cohort date and timezone. DLS must approve the definition of each metric (e.g. submission-to-completion elapsed time vs. SLA time excluding verified pauses). Support monthly, quarterly and annual performance, officer workloads, pending requests, processing times, contracts/risk/expiry, opinions, compliance and litigation/outcomes. Management defaults to aggregates with approved suppression of small sensitive groups and no drill-through. PDF/XLSX generation, retrieval and expiry are separately authorised and audited; no confidential spreadsheet is emailed automatically.

## 5. Workflows and checklist

See the [transition tables, approval invariants and digital checklist design](workflows.md). Staff requests go through triage, assignment, authorised review and controlled issue. A contract's intake request and contract lifecycle are linked: **Head of Legal Services clearance is always explicit and version-bound; conditional clearance is not authority to execute; clearance is not the signature of an authorised signatory.** The official checklist `TBC/DLS/CRF/001/2026` remains Internal Use Only and is not scored to decide legal approval.

## 6. Security architecture

| Boundary / risk | Proposed control and assurance |
| --- | --- |
| Identity, login abuse and stale access | Prefer approved institutional SSO (OIDC, or approved SAML via an adapter). Verify issuer and stable subject, not email alone. Require MFA for privileged legal/ICT functions; use MFA for staff wherever supported. Require recent verified MFA for final decisions and privileged grants. Any local contingency is separately approved, uses Argon2id, supervised enrollment/recovery and strong MFA for privileged operations. Rate-limit by account and source with monitored, safe recovery and generic failure messages. |
| Sessions and browser/API | Rotate opaque server-side sessions at sign-in/privilege changes; use `Secure`, `HttpOnly`, `SameSite` cookies, idle/absolute expiry and revocation on disablement/role changes. Enforce CSRF tokens and origin checks on mutations, same-origin by default, HTML escaping/sanitisation, restrictive CSP/frame policy, approved host allowlist, and ORM/parameterised queries. No credentials or reusable bearer tokens in browser storage. Integration clients use separate short-lived service identities/scopes and authenticated channels. |
| Network and data | TLS and HSTS at the approved edge; authenticated protected internal traffic where applicable. Separate edge, app, data, scanning and admin networks; deny default ingress/egress and keep management paths private. Encryption at rest for DB, files and backups; additionally encrypt designated sensitive columns/files with institutional key management and rotation. Keep secrets outside source and images. Review whether text may enter search indexes; highly restricted or field-encrypted content has metadata-only search unless a secure approved index is established. |
| ICT vs. legal privilege | Separate application administration, infrastructure access, deployment and keys. Privileged access management, individual operator identities and dual-controlled recovery limit technical power; do not promise that RBAC can restrain a privileged OS or deployment compromise. Break-glass recovery is time-bound, approved, monitored and reviewed, never a legal approval route. |
| Unsafe files and document disclosure | Stream uploads to quarantine; per-type and user quotas, allowlisted extensions and verified content type, archive/decompression limits for Office formats, isolated malware scan and renderer without external calls. Start with PDF, DOCX, XLSX after ICT validation; reject unsupported, executable or uninspectable content. Use opaque storage keys; original names are metadata. Clean immutable versions only. Preview safe derivatives; authorise each preview/download on version, classification and link/release, audit access before streaming, no public buckets or reusable links, no shared caching. |
| Privacy, retention and evidence | Map purposes/data categories/processing, minimise employee and counterparty data, document controller/processor roles and assess impact with the Data Protection Officer (DPO). Use proposed working labels Internal Use Only, Confidential and Restricted/Privileged pending TBC policy. Freeze issued versions, hash originals, record evidence custody; holds block disposal and may span related records. Reviewed schedule and dual approval for transfer/destruction, including search derivatives and backup expiry; no automatic purge before a policy is approved. |
| Audit, misuse and observability | Append events with actor/service and acting role, action, record/version, UTC time, outcome, request/session correlation, trusted network context if available, and allowed before/after values or version references. Include approval/return/rejection, role/config changes, viewing sensitive content, preview/download and export. Never log secrets, tokens or privileged narrative. Runtime has append-only audit permissions, restricted read via audited projections and a separately controlled tamper-evident archive. Sensitive access fails closed if its audit cannot be recorded; high-impact mutations and audit commit atomically. Redacted monitoring covers failed logins, access anomalies, queues, scan failures, audit lag and restore health. |
| Recovery and incidents | Encrypted DB point-in-time recovery, object version recovery and protected configuration/key recovery at approved sites. Rehearse a coordinated restore of data, metadata, audit and jobs; reconcile versions before resuming downloads and reminders. Approve RPO (acceptable data loss) and RTO (restoration time) from a business impact review; exercise branch connectivity and manual deadline continuity. Run an incident playbook for detection, containment, evidence preservation, authorised escalation/reporting, restore and follow-up. |
| Release security | Protected branches, reviewed builds, dependency/licence inventories, vulnerability/secret scans, secure test data and separated deploy rights. System and security acceptance before production, remediation/retest, annual assessment and reviews after major change. ICT verifies environment and approved operators; DPO/Legal validate privacy and records controls. |

## 7. e-GA Version 3.0 assessment preparation

See the [clause-to-evidence mapping and legal source register](ega-v3-mapping.md). It uses the Authority's published **eGA/EXT/APA/005, Version 3.0 (February 2026)** [1](https://www.ega.go.tz/uploads/standarddocuments/sw-1773258298-FINAL%20Reviewed%20Standards%20and%20Guidelines%20for%20Development%20Acqusition%20Operation%20and%20Maintanence%20of%20Government%20Applications%20-%2018%20-2-2026.pdf). Two early decisions: evidence of support for different databases/OSs, and a government-approved source repository with institutional ownership and recovery. The Arena checkout is not evidence of approved hosting or authorised source custody. No TBC production records, secrets or restricted attachments belong in Arena or an unapproved repository.

## 8. Phased delivery and verification

Use **formal baseline/sign-off gates with iterative implementation within each phase**. TBC's ICT Management Unit and DLS co-own the backlog; report evidence to the Steering Committee and submit to e-GA through the approved process. No schedule or budget assumed. Move to the next module only after its positive and negative tests, review and required sign-off; unresolved gaps remain explicit, not simulated as implemented functionality.

| Phase | Outcome | Gate / evidence |
| --- | --- | --- |
| 0 — Review and discovery | Validate actual TBC procedures, reuse options and stakeholders; decide scope, priority, hosting/identity constraints and initial policy. Baseline BRD, SRS, SDD/data model and security/architecture evaluation; written hybrid SDLC decision and initiation/clearance route. | Architecture comments resolved; DLS/ICT authorise build against reviewed requirements and applicable institutional/e-GA gates. **Current turn stops here at proposal.** |
| 1 — Foundation | Versioned DB migrations, identity, organisation, roles and audit; staff/admin shells and real scoped dashboard queries. Implement shared document/security primitives early. | Verified login/offboarding, cross-scope and privileged-access denial, database portability smoke, navigation and EN/SW accessibility. Empty states reflect actual data. |
| 2 — Core legal operations | Persist requests, assignment, public/internal history, in-app notifications, contracts, changes, full checklist, versioned approvals and release. | End-to-end submission to issue; unique concurrent references, safe retry, no approvals skipped, version revocation/re-review, official form comparison and branded PDF acceptance. |
| 3 — Specialist operations | Litigation, opinions, rights, compliance, and full repository incl. reviewable evidence, classifications, versions and holds. | Scope tests for strategy/opinion confidentiality; appeal/hearing and renewal records; verified-source compliance publication; controlled file access and archive/hold tests. |
| 4 — Reporting and scheduling | Scoped management dashboard, calendar, jobs/reminders/escalation, advanced search, PDF/XLSX reports and approved integrations. | Reconciled totals, permission revocation and export safety, EAT reminder correctness, repeated delivery dedupe, sandbox integration tests where authorised. |
| 5 — Acceptance and rollout | System, integration, performance and security verification; UAT and System Acceptance Testing (SAT), training, migration rehearsal, restore, cutover/rollback and deployment assessment. | Signed UAT/SAT and readiness recommendation, tracked remediation, approved host/domain and formal institutional/e-GA requirements satisfied. No production solely on developer approval. |
| Operate and improve | ICT service desk and operational agreement, incident/change control, monitoring, restore drills, annual assessment, post-implementation review and benefit tracking. | ICT retains evidence and secures each change through initiation, impact analysis, disposition and tested release/rollback. |

**Documentation pack by gate.** Phase 0 baselines BRD/SRS, use-case package/diagrams, workflow diagrams, SDD, database/data dictionary, interface and operational requirements, security architecture and draft test plan. Phases 1–4 maintain versioned API documentation, migration/installation guidance, requirement-to-test traceability and end-user/legal officer/operator manuals. Phase 5 delivers signed reports (using UAT and SAT report templates), administrator and deployment guides, backup/DR and incident plans, change procedures and EN/SW training materials. This proposal starts these artifacts; it does not stand in for signed requirements or test results.

### Initial requirement-to-test traceability

Planned acceptance cases, not results. The signed SRS must expand each group into individual requirement IDs and link each to inputs, expected outputs, environments, result/evidence and defects.

| Scope | Example planned verification |
| --- | --- |
| IAM and access | **T01** SSO/MFA, logout, idle expiry, staff move/disablement; **T02** deny unauthorised list/search/detail, strategy, attachments, queues and exports across roles/departments. |
| Requests and contracts | **T03** idempotent submit and concurrent numbering, request-info/return/reject, no skipped-stage or self-approval; **T04** checklist fidelity, save/resume, snapshot PDF; **T05** conditional clearance, superseded version and post-execution gates. |
| Specialist registers | **T06** litigation orders/hearings and opinion revision/issue with restricted access; **T07** rights expiries/restrictions and officer verification of compliance references. |
| Documents, automation and reporting | **T08** quarantine/scan, direct-object denial, version/release revocation and hold; **T09** reminder, escalation and retry across EAT boundaries; **T10** seeded totals/filters, report disclosure, authorisation at fetch and safe XLSX/PDF. |
| Cross-cutting | **T11** bilingual, accessible keyboard/mobile journeys and regional performance; **T12** migrations/constraints/concurrency on agreed database and OS combinations; **T13** tested restore, key recovery, incident rehearsal; **T14** migration reconciliation/rollout rollback; **T15** audit completeness, integrity, restricted exports and monitoring. |

## Review decisions and missing requirements

| ID / when | Decision needed | Proposed owner and conservative default |
| --- | --- | --- |
| D01 · before build | Confirm the institutional sponsor, actual legal processes, reuse options, internal staffing and project clearance/acquisition route. Agree priority and minimum useful pilot scope. Is there an approved shared solution that already covers part of LSMS? | Steering Committee/ICT with DLS; no duplicated platform or external procurement assumed. |
| D02 · before IAM design is baselined | Identify identity/directory and HR sources, TBC directorates/departments/offices, worker types, sponsor and deprovisioning SLA. Decide department-head scope, multi-office/acting roles, sealed matters, auditors and external advocates. | ICT, HR, DLS; deny unknown users/edges, restrict to own view, no external accounts. |
| D03 · before workflow design is baselined | Provide the controlled checklist copy and approved bilingual glossary; confirm intake criteria, any permitted omissions, escalation/holiday/SLA rules, conditions, delegations, Head absence, external vetting where applicable, executive approvals and signing authority. Define numbering and completion/reporting semantics. | DLS with Procurement and Records; no uncodified waiver, no self-approval or conditional execution. |
| D04 · before real data | Provide approved classification, retention, holds/disposal, evidence custody, privacy notice and access policy; determine DPIA, registration status, lawful processing, breach routing and any transfer constraints. | DPO, DLS and Records; no real data in unapproved environments and no auto-purge. |
| D05 · before infrastructure procurement or integration | Confirm hosting and secondary recovery site, approved source custody, GovNET/remote paths, DNS/TLS, KMS, storage, SIEM, operator ownership and approved integration providers. | ICT and e-GA liaison; synthetic isolated development only until approvals. |
| D06 · before test baseline | Confirm concurrency and upload/storage growth, regional network profiles, availability/performance targets, backup RPO/RTO, reporting calendars/KPIs, accessibility criteria, brand assets and acceptance signatories. | ICT, DLS/Management, Communications; targets to be measured and approved, not asserted here. |
| D07 · before data migration or rollout | Inventory spreadsheets/paper/e-office systems, originals and authority of historical status, dedupe and scope mapping, legal holds and cutover. Plan reconciliation, training, support and who signs off imports. | DLS/Records and ICT; archive originals; record provenance, never fabricate old outcomes or approvals. |

### Integration dependencies and fallback

No provider, contract, credentials or endpoint is confirmed. For each approved integration define the source of truth, minimum fields, security, correlation/reconciliation, retry/timeout, revocation, support and test acceptance. Avoid direct cross-system database writes.

| Candidate boundary | Main challenge | Proposed safe fallback / next step |
| --- | --- | --- |
| Identity, HR roster and regional connectivity | Stable person/organisation codes, multiple appointments, delayed departures, MFA assertions and branch VPN bandwidth | Verify sandbox claims and sync/deactivation first. ICT-managed, individually approved provisioning if available; deny privileged actions without verified MFA. Rehearse regional access. |
| Approved hosting and repositories | Storage/KMS support, retention-lock semantics, restore dependencies and source repository/DR custody | Load only synthetic fixtures until both platform and recovery paths have been approved and rehearsed. |
| Email/SMS | Approved gateway and sender, delivery reliability, recipient drift, metadata disclosure | In-app is authoritative. Channels disabled until approved; generic messages with authenticated links only, no subject matter, party names or attachments. |
| Government data exchange (GovESB) | Onboarding, GISP registration where applicable, compatible schemas, disclosure and consent/authority where needed | ICT/e-GA liaison confirms obligations and agreements. Use tracked manual transfers of approved extracts until authorised; no automatic exchange of privileged records. |
| Procurement/finance and signature or vetting services | Authority thresholds, version mismatch and which system records approval, budget confirmation or original signatures | No assumed API or legal threshold. Use signed-off manual evidence against the exact version; block execution if a required gate remains unverified. |
| Courts, advocates and e-office/records | No verified court-feed/API, file custody, sensitive contexts, original record location and duplicate identifiers | Legal officer records verified hearing/filing data with source and timestamp. Track transfers and reconcile manifests. No external portal or automated court filing at baseline. |
| Broadcast media catalogue / rights systems | Asset IDs, territory, platform, rights windows, reuse scope and soundtrack/third-party rights | LSMS records licences, rights and expiry, not media binaries. Use verified asset references before any sync; internal reminders operate independently. |

**Requested decision:** Approve the proposal for formal requirements/design baselining, or return changes. Prioritise D01–D05 with DLS, ICT, Records and DPO before committing the design. Application implementation remains paused until authorised.
