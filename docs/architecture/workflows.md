# LSMS — workflow and digital checklist design

**Draft for DLS review** · [Architecture proposal](proposal.md) · [Logical schema](data-model.md)

## Shared workflow rules

Each matter has one or more coordinated lanes (staff intake plus a specialist lane when applicable). State changes are named server commands, not edits to a status field. A command validates effective permissions, current stage, record scope, expected revision, reasons and required evidence. It locks the active instance, then commits state, assignments/decision, audit and notification outbox in one DB transaction. Clients may retry safely; a decision cannot apply to a newer revision than the approver saw. Superseded decisions are retained. Reassignment changes current responsibility, not past decisions. Audit records are not the staff timeline.

DLS can propose versioned rules for intake, routing, due dates, authorised portfolios, senior review and escalation. Publish a new policy only after review by a different authorised person and a tracked change. Store the active version with each instance; an in-flight instance stays pinned unless a reviewed migration preserves history and all guards. Configuration cannot disable conflict/role/clearance checks, let the system vote, or substitute a configurable status name for evidence. Timers and integration accounts may create tasks, alerts or escalation notices only. Distinct reviewers are required for final decisions; if no valid reviewer exists, queue and escalate, not auto-approve.

## 1. Legal request

```text
Staff draft → Submitted → Under Preliminary Review → Accepted
Accepted → Assigned → Under Legal Review → Awaiting Head of Legal Services Review

Preliminary or Legal Review → Awaiting Additional Information
                            → saved review stage after verification
Under Preliminary Review → Rejected (triage decision)
Awaiting Head of Legal Services Review → Under Legal Review (return)
                                       → Approved → Completed (controlled issue)
                                       → Rejected (Head decision)
Completed or Rejected → Closed
```

| Command and resulting state | Actor, precondition and effect |
| --- | --- |
| Draft → **Submitted** | Staff or authorised initiating officer. Validate title, department/directorate/office, requesting officer and contact, category, description/background, desired completion date, priority, documents and accuracy declaration. Employee ID only when applicable. Snapshot submission; generate the unique reference at commit and notify scoped intake. Drafts are not counted as submitted. |
| Submitted → **Under Preliminary Review** | Named intake officer. Capture verification against category rules. Required uploads that are pending scan do not satisfy completeness. Confirm authoritative department and correct misrouting without erasing origin history. |
| Preliminary (or Legal) Review → **Awaiting Additional Information** | Scoped verifier/assignee sends an approved staff-visible question with deadline, keeping internal reasons separate. Staff may answer or add clean evidence; the answer queues verification, not acceptance. Return to the saved review stage only after the authorised verifier confirms the response, and invalidate any proposed recommendation that depended on changed material. |
| Preliminary Review → **Accepted** / **Rejected** | Authorised intake officer with reasons. Accept only if mandatory evidence passes; any waivable omission needs an approved rule, explicit supervisory sign-off and reason. Otherwise request information or reject with a reviewed staff-facing reason. No waiver can replace a mandatory legal or approval step. |
| Accepted → **Assigned** → **Under Legal Review** | Senior/Head or delegated assigner selects eligible officer, priority and due date and creates an assignment. Active assigned officer begins work; notes and evidence remain internal by default. Reassignment and deadline changes have reasons and history; escalation notifies the approved supervisor. |
| Under Legal Review → **Awaiting Head of Legal Services Review** | Assigned officer locks the recommendation/version and evidence after completing required supervisory checks. For a contract, opinion or other linked workflow, bind the corresponding domain approval package; do not create two independent votes on the same recommendation. |
| Awaiting Head Review → **Approved**, **Under Legal Review** (return), or **Rejected** | Head or valid acting Head only, with step-up MFA, distinct from requester and author and without a recorded conflict. Approve the frozen recommendation, return with changes and a new review round, or reject with retained internal and safe staff-facing reasons. Only the linked authorised domain decision can drive approval for specialist deliverables. |
| Approved → **Completed** → **Closed** | Issuer publishes the exact authorised version(s) and/or the reviewed staff response; notify eligible recipients and audit release. A material change before issue revokes eligibility and requires a new decision. Close from Completed or Rejected only by scoped DLS/records workflow with reason and retention/hold checks; retain final outcome. Opening a related issue after closure creates a linked matter under approved policy rather than rewriting history. |

The staff timeline contains only the submission, safe status/date milestones, explicitly published correspondence and released versions. Head-of-department access does not imply access to attachments or notes. Closing does not convert a rejection into completion in reporting. A court hearing or fact update is not a legal clearance; a legal response issued to staff goes through its required approval path.

## 2. Contract lifecycle and execution gate

| Stage | Guard and transition |
| --- | --- |
| Submission → preliminary verification | Staff intake or authorised legal registration; validate required documents and authority. Incomplete submissions use the request-information/rejection path above. A paper-origin file is identified as such and cannot claim past electronic approvals. |
| Accepted → registered → assigned | Create a contract record with its own immutable reference (proposed `TBC/DLS/CTR/{EAT year}/{sequence}`; confirm with Records), category, counterparty and first version. Link to the originating request if present. Assign an eligible legal officer with review due date. |
| Legal review → recommendation | Verify draft, facts and attachments, complete Parts I–X, explicitly set risk in XI and complete recommendation in XII. Required checks have a response; record justification for No and N/A (proposed policy). A No may require mitigation, further negotiation or rejection; it does not generate a decision. Freeze review package of contract version, checklist, evidence versions, risks, conditions, and recommendation. Verify any required Senior review. |
| Head review | With step-up MFA and a distinct named actor, record exact version and reason. **Cleared** only for a currently reviewed revision and all required prior gates. **Returned for amendment/further negotiation** starts a new review round. **Conditional** records conditions, owner and due date but is not executable; verify conditions and obtain a new explicit clearance (same version if unchanged, otherwise a new version). **Not recommended** is a terminal result for that review, retained with reason; communicate only approved disposition. The live staff request is updated transactionally by the applicable clearance/rejection event. |
| Cleared → execution recorded | Legal clearance is not contract execution. Record the applicable separate vetting, procurement, board, executive and signatory evidence where TBC's verified policy requires it; do not invent thresholds. Strategic/Board-Level risk is escalated and assigned as needed, never auto-cleared or a management permission shortcut. Verify that the signed counterpart matches the currently cleared text (signatures may change file bytes). If substantive terms changed, require clearance of the new version first. Capture signing parties/dates and evidence; do not label an app click as a legal signature. |
| Execution → monitoring and register | Verify Part XIII after execution; record authorised contract manager, schedule, originals, effective/expiry dates and contract register entry using the same reference. The earlier registration is intake; marking the formal post-execution register entry is a distinct event. Renewal/expiry and obligations drive calendar/reminders. Request completion may occur after issuing the legal deliverable and before execution. |
| Amendment, renewal, closure and archive | Maintain stable base reference, amendment/renewal records with prior and proposed versions and a new approval round for each change to terms. Keep effective versions and dates distinguishable from proposed drafts. Record expiry/termination or renewal and monitoring completion. Archive with classification and retention, not destructive closure; active holds prevent disposal. |

A reviewer can return work before approval; an unissued cleared package can be superseded only by an explicit, audited command. Never carry a decision onto changed text or evidence. Issued packages remain immutable; a correction is a newly reviewed issue, with policy-controlled withdrawal/supersession of the earlier release. When an external decision is required but its authority or evidence is not verified, block the affected gate and escalate to DLS.

## 3. TBC official contract review form

**Reference:** `TBC/DLS/CRF/001/2026` · **Owner:** Directorate of Legal Services · **Classification:** Internal Use Only. Preserve labels, order and wording from the controlled copy; use the master specification as the design inventory until DLS verifies the source and any translation. More restrictive matter classifications still apply to responses and evidence.

| Form component | Digital representation |
| --- | --- |
| **A. Contract Identification Details** | Capture/display title, contract reference, requesting directorate/department, responsible officer, counterparty name, type, value and currency, duration, submission date, required completion date and assigned legal officer. Freeze identification values in each review snapshot. |
| **I. Preliminary Review** (8); **II. Contracting Parties Review** (7); **III. Legal and Regulatory Compliance Review** (8); **IV. Contract Terms Review** (7) | Ordered prompts as supplied, with Yes, No or N/A, remarks, and permission-checked supporting attachment(s). Draft unanswered is distinct from N/A. |
| **V. Financial Review** (8); **VI. Intellectual Property and Content Review** (8); **VII. Risk Allocation Review** (8); **VIII. Digital, ICT and Data Protection Review** (8) | Same response model, original wording and evidence rules. Keep appropriate monetary and rights/territory data in their own structured records. |
| **IX. Termination and Dispute Management** (10); **X. Counterparty Due Diligence** (7) | Use the same response model; preserve every item even when a reviewer marks it N/A. |
| **XI. Risk Classification** | Human-selected Low Risk, Medium Risk, High Risk or Strategic/Board-Level Risk; register rows for category, description, likelihood, impact, mitigation and responsible person. Scale and escalation to be approved. |
| **XII. Approval and Legal Clearance** | Recommendations: Approved – Legally Acceptable, Approved Subject to Amendments, Requires Further Negotiation, Not Recommended for Approval. Record conditions/amendments, legal officer name, authenticated approval/attestation and server approval date; separately record Head name, decision and date. Preserve every round and return. Typed names alone are not an approval. |
| **XIII. Post-Execution Requirements** (6) | Same response/remarks/evidence model after execution: authorised signatures, reference assigned, contract register, manager appointed, monitoring and original archived. Post-execution facts do not block completion of the *pre-clearance* section, and the software does not infer a Yes. |

The inventory has 79 response prompts in Parts I–X and six in XIII, plus the contract identity, risk and decision fields. Use stable item IDs and immutable template revisions. A lawyer autosaves against server revisions, sees saved/pending/error state, resumes from the DB and resolves concurrent updates without overwriting another reviewer. Lock reviewed revision, identified attached versions, actor, authenticated intent, and trusted timestamps with a digest. Part XIII is a separate phase/instance linked to the executed version and clearance; it cannot alter a prior review. Produce a printable branded PDF from the frozen review and completed execution record, with reference, form version, approval dates, classification, and approved logo when available. Any pre-execution PDF must clearly show incomplete post-execution sections. Do not expose attachments in PDF links, score the form into approval, or release the internal checklist to staff by default.

## 4. Other specialist lanes and reminders

- **Opinions:** register and assign → draft/research using verified references → senior review → Head approval (baseline; any permitted variation needs a separately approved policy revision) → issue frozen approved version → archive. Version every revised draft/issue; separately authorise sensitive opinions and each release audience.
- **Litigation:** register/assign → record hearings, orders, filings, custody, costs, settlement and outcomes → close with approved disposition and any appeal/obligation action reviewed. A settlement requiring authorisation waits at its own human gate. Legal strategy is a separately restricted view and evidence class, not a staff status or calendar detail.
- **IP/rights and compliance:** register/assign, verify provenance and approval, track licence scopes/expiry or obligations/evidence/corrective action, and record renewal or review. Legislation/obligations are entered and verified by named authorised officers; no fabricated seed law or automatic compliance conclusion.
- **Notifications/tasks:** submission, assignment, pending review, approval, rejection, requests for information, court hearing, expiry, overdue and escalation produce scoped in-app events. Schedule against the versioned EAT working calendar. Jobs deduplicate on event/recipient/channel/due instance, revalidate permission and current deadline at send time, cancel stale reminders, retry transient failures and flag exhausted jobs for ICT. Approved external channels use redacted templates. Delivery or failure never grants access or advances legal status.
