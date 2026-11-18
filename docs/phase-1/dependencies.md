# Phase 1 dependency inventory

The application pins direct dependencies in [`requirements.txt`](../../requirements.txt); [`requirements-dev.txt`](../../requirements-dev.txt) adds test/maintenance tools. The browser is not an application or build dependency; the test-only versions and reproduction steps are in the [verification report](verification.md). No CDN requests are needed at runtime.

| Direct dependency | Version at verification | Upstream license metadata |
| --- | --- | --- |
| Django | 5.2.19 | BSD-3-Clause |
| Django REST framework | 3.16.1 | BSD |
| argon2-cffi | 25.1.0 | MIT |
| cryptography | 47.0.0 | Apache-2.0 or BSD-3-Clause |
| psycopg and psycopg-binary | 3.3.9 | LGPL-3.0-only |
| PyOTP | 2.9.0 | MIT |
| tzdata | 2026.6 | Apache-2.0 |
| polib (development) | 1.2.0 | MIT |
| Ruff (development) | 0.17.5 | MIT |
| [Inter](https://github.com/rsms/inter) Latin variable WOFF2 from `@fontsource-variable/inter` | 5.3.0 package | [SIL Open Font License](../../static/lsms/fonts/OFL-Inter.txt) |

This inventory lists direct components, not a signed SBOM, licence clearance or vulnerability assessment. Create and review a complete release inventory and approved patch baseline before production.
