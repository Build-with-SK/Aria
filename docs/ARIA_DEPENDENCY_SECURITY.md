# ARIA dependency security — Phase 2, 2026-10-17

Candidate remediation; deployment environment remains BLOCKED until its owner
installs and audits the candidate. The running application's venv was not changed.
No audit ignores, forced resolver updates, or disabled tests were used.

## JavaScript

`npm audit --json`: before 9 affected package nodes (5 high, 4 moderate), after 0.
Counts include dependency chains, not nine distinct vulnerabilities.

| Finding | Exposure and disposition |
| --- | --- |
| [braces recursion exhaustion](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), high | Tailwind's glob/watch dependency chain; developer/build input, absent from shipped JS. Removed unused Tailwind and its configuration. No Tailwind directives or generated utility classes were used. |
| [selector parser CPU exhaustion](https://github.com/advisories/GHSA-rj75-hqrm-r3gf), moderate | Same unused build pipeline; removed with Tailwind. PostCSS/autoprefixer retained. |
| [router redirect](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6), moderate | Shipped library; current Link destinations are fixed local paths with encoded symbols, limiting exposure. Upgraded existing react-router-dom to patched 7.18.4. |
| [router SSR hydration injection](https://github.com/advisories/GHSA-337j-9hxr-rhxg), moderate | Production is client rendered, so vulnerable SSR hydration path is not used. Same upgrade removes advisory. |

The router major is necessary because the published fix has no 6.x backport.
Its package metadata supports React >=18 and Node >=20 (project uses 18 and 24).
No new direct npm dependency. Before router upgrade the generated CSS was
byte-for-byte identical to Phase 1 after removing Tailwind. After upgrade the
route/Research/freshness, technical and execution SSR checks and staging build
pass. Actual browser verification is recorded separately in the Phase 2 handoff.

## Python

Existing audit tool: pip-audit from the isolated verification tooling venv,
with `--path` targeting the installed application packages. Before: 119 records
across 14 distributions, **74 distinct (package, advisory-id) pairs**. Several
records are duplicated by the advisory feed; do not call them 119 unique CVEs.
pip-audit does not provide severity scores in this output. Priorities below
are project exposure assessments, not invented upstream CVSS scores.

| Package before | Records / distinct IDs | Project priority and action |
| --- | --- | --- |
| aiohttp 3.14.1 | 6 / 3 | Medium: malicious upstream responses/WebSockets; ARIA is not an aiohttp server. Constrain >=3.14.3,<4 when installed. |
| anyio 4.13.0 | 2 / 2 | Medium: IDN TLS and subprocess worker availability conditions. >=4.14.2,<5. |
| chromadb 1.5.9 | 5 / 4 | High impact if exposed as a Chroma server: code execution/tenant authorization. ARIA uses embedded PersistentClient, not Chroma HTTP server or remote model repositories. No fixed version reported. Optional package excluded from base install. Remaining deployment exception below. |
| GitPython 3.1.50 | 32 / 24 | High impact command/config injection with untrusted repositories/options; no direct GitPython import found in application. Transitive optional tooling exposure. >=3.1.60,<4. |
| multidict 6.7.1 | 1 / 1 | Medium parser/container exposure through HTTP dependencies. >=6.9.1,<7. |
| oauthlib 3.3.1 | 2 / 1 | Conditional OAuth endpoint exposure; no application import found. Fix requires 4.x. Not added to application and not upgraded blindly in shared environment. If retained by deployment, owner must audit its actual consumer or remove it. |
| Pillow 12.2.0 | 25 / 13 | High impact for malicious image decoding, low observed exposure (no public arbitrary image decoding flow identified). >=12.3.0,<13. |
| pip 24.0 | 12 / 6 | Installation/build tooling, not API request path. Fresh venv bootstrap upgrades pip. Old shared bootstrap remains deployment work. |
| python-multipart 0.0.28 | 6 / 3 | High priority: network-facing upload/form parser (authenticated endpoints still accept attacker influenced data). >=0.0.31 in all manifests. |
| setuptools 65.5.0 | 8 / 4 | Installation/build tooling, malicious source/archive inputs. Fresh bootstrap upgrades; no application dependency added. |
| soupsieve 2.8.3 | 6 / 4 | Medium scraper parsing availability with malicious HTML/selectors. >=2.9.0,<3. |
| starlette 1.0.0 | 10 / 5 | High priority web framework parsing/routing/security behavior. >=1.3.1,<2; resolver must satisfy FastAPI too. |
| torch 2.12.1 | 1 / 1 | Advisory describes critical local JIT memory corruption. Optional training stack, no `torch.jit.script` usage found; untrusted local code/model execution must remain prohibited. Fix 2.13 is outside base install; deployment exception. |
| urllib3 2.7.0 | 3 / 3 | High priority upstream streaming exhaustion/proxy TLS confusion; downloader/requests exposure. >=2.8.0,<3. |

`constraints-security.txt` is included by all three manifests. Constraints only
bound packages that are installed; they do not install optional software. Plotly
stays below 7 to avoid an unrelated major migration. Exchange calendar pin is
unchanged. The reproducible candidate install is a new, empty venv:

```powershell
python -m venv <temporary-directory>
<temporary-python> -m pip install --upgrade pip setuptools
<temporary-python> -m pip install -r requirements.txt -r requirements-ci.txt
<temporary-python> -m pip check
pip-audit --path <temporary-site-packages> --format json
```

The complete candidate audit/regression results and resolved versions are
recorded in the Phase 2 handoff after installation finishes. Initial web/live
gate/calendar/runtime regressions in the prior minimal environment: 128 passed,
1 skipped, 24.48s. This does not substitute for testing the upgraded environment.

## Remaining deployment exceptions

These are scoped, justified restrictions, not accepted blanket release waivers.
Do not expose a Chroma server, permit remote model trust, consume untrusted
PyTorch code/models, or keep unused OAuth/build packages solely because their
API paths were not observed here. Re-audit the exact deployment environment;
remove unused optional distributions or approve their consumer-specific plan.
No credential, service restart, production install or external broker operation
was performed. A clean base audit cannot certify the existing optional-heavy
environment. Raw machine-readable audit results remain in private Phase 2
evidence; no private package paths or credentials are checked in.
