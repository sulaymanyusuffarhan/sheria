# TBC Legal Services Management System (LSMS)

**Phase 2 development build.** LSMS is a modular Django 5.2 LTS application with a staff portal (`/portal/`) and a legal workspace (`/admin/`). Staff can submit and track requests, and Legal Services can triage, assign, review and issue approved responses. Contract requests have private, versioned evidence, a digital checklist and a distinct authenticated Head decision. Production and real-data use are not authorised.

**Checklist source:** The build preserves A and I–XIII of `TBC/DLS/CRF/001/2026` using the available design inventory. The controlled form is not in the checkout. Prompt wording and translations are draft pending DLS reconciliation. Clearance is not contract execution.

## Try the isolated demonstration

Use the Arena preview if it is running, or run locally with Python **3.11+**. No credentials are needed for the synthetic role picker, and demo identities have unusable passwords.

```sh
python -m venv .venv
. .venv/bin/activate  # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
export LSMS_ENV=development LSMS_DEMO=1  # PowerShell: $env:LSMS_ENV='development'; $env:LSMS_DEMO='1'
# Disposable demo only: generate once, reuse it with this database. Keep the key out of Git.
mkdir -p .local
(umask 077; test -f .local/demo-document.key || python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > .local/demo-document.key)
export LSMS_DOCUMENT_KEY="$(cat .local/demo-document.key)"
python manage.py migrate --noinput
python manage.py seed_demo
python manage.py seed_phase2_demo
python manage.py runserver
```

Open <http://localhost:8000/>. Choose **Legal dashboard**, **Staff portal**, or **ICT console**. Expand **More demonstration roles** for the other six. To switch roles, sign out first. Try **Staff portal → New legal request** or **Legal dashboard → Contract register**. The [workflow guide](docs/phase-2/guide.md) walks through the employee, Senior, legal officer and Head views. Existing synthetic decisions are examples, not legal approvals. Changes to this sample database persist; run with a **new** `LSMS_DB_PATH` and reseed for a clean copy. Never put institutional data in the demo database.

`LSMS_DEMO=1` uses `.local/demo.sqlite3` by default and refuses to run if the database contains non-demo identities, directory units or matters. `seed_demo` and `seed_phase2_demo` are repeatable; it does not erase the changes made during a demo. `LSMS_PREVIEW=1` is restricted to the synthetic development SQLite mode. The Arena preview is temporary and not institutional hosting.

## Run the tests

```sh
export LSMS_ENV=test LSMS_DEMO=0 LSMS_PREVIEW=0  # Use a dedicated test environment.
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python -m ruff check .
python -m ruff format --check .
python tools/compile_i18n.py --check
```

The default test database is freshly migrated SQLite. PostgreSQL is the proposed primary. Set the `LSMS_DB_*` variables for an **isolated PostgreSQL test instance** or use the disposable Linux smoke runner: `python -m pip install pgserver==0.1.4` then `python tools/postgres_smoke.py`. See [Phase 2 verification](docs/phase-2/verification.md) for results, browser replay and limitations.

## Configuration and operator notes

| Purpose | Environment variables |
| --- | --- |
| Separation | `LSMS_ENV`: `development` (default), `test`, `staging`, `production`; `LSMS_DEMO=1` and `LSMS_PREVIEW=1` are development-only. Use separate DBs and keys. |
| Database | `LSMS_DB_ENGINE=sqlite` for development/tests, `LSMS_DB_PATH` optional. `LSMS_DB_ENGINE=postgresql` with `LSMS_DB_NAME`, `LSMS_DB_USER`, `LSMS_DB_PASSWORD`, `LSMS_DB_HOST`, `LSMS_DB_PORT`, `LSMS_DB_SSLMODE`. Staging/production require PostgreSQL. |
| Authentication | `LSMS_SECRET_KEY`: externally managed stable secret (at least 50 characters outside local use). `LSMS_MFA_KEY`: separately managed Fernet key for **local** TOTP enrolment. No default identities, passwords, tokens or TOTP keys. |
| Private documents | `LSMS_DOCUMENT_KEY`: separate Fernet key for encrypted document contents. `LSMS_CLAMD_SOCKET`: private ClamAV UNIX socket for non-demo scanning; without a verified verdict, evidence stays quarantined. |
| Web | `LSMS_ALLOWED_HOSTS` and `LSMS_CSRF_TRUSTED_ORIGINS`: comma-separated approved values. `LSMS_TRUST_TLS_PROXY=1` only behind a proxy that strips and sets the forwarded protocol header. Arena development previews allow the known sandbox host on `LSMS_PREVIEW_PORT`. |
| Localisation | `LSMS_TIME_ZONE` defaults to `Africa/Dar_es_Salaam`. EN/SW controls the interface, not the language of user-entered record text. Edit `locale/sw/LC_MESSAGES/django.po` and rebuild via `python tools/compile_i18n.py`. |

For a **non-demo local or controlled staging** test, unset `LSMS_DEMO`/`LSMS_PREVIEW`, select a separate DB and migrate. Establish operator/approver identities in a private, interactive terminal with `python manage.py enroll_local <username> --new-role ict --totp` and `--new-role head --totp`, respectively, under separate, approved custodians. This command is for trusted DB operators; record authorisation before bootstrapping any role. The key and one-time code must not go into chat, scripts, screen recordings or deployment logs. Create the organisation directory and employees from ICT; for a verified employee run `python manage.py enroll_local <username>` without `--new-role` (`--totp` for an elevated role). In-app delegable grants require an independent decision before ICT activates them. No Django superuser route is exposed. Production sign-in is **blocked** until an approved institutional IdP and MFA integration are implemented and accepted.

## Documents

- [Phase 2 workflow and operator guide](docs/phase-2/guide.md) · [Phase 2 verification and remaining gates](docs/phase-2/verification.md)
- [Phase 1 verification and gate](docs/phase-1/verification.md) · [Versioned API contract](docs/api/v1.yaml) · [Dependencies](docs/phase-1/dependencies.md)
- [Architecture proposal](docs/architecture/proposal.md) · [Logical data model](docs/architecture/data-model.md)
- [Workflows and TBC checklist design](docs/architecture/workflows.md) · [e-GA v3 assessment mapping](docs/architecture/ega-v3-mapping.md)

The proposal’s institutional sign-in, controlled form, privacy, custody, infrastructure, acceptance and e-GA gates remain open. Run only with synthetic data until DLS, ICT and the responsible control owners give the required approvals.
