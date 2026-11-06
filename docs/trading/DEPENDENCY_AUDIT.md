# Dependency audit — 2026-11-06

## Shared existing Python venv (unchanged)

`uvx pip-audit --path <shared-venv>/Lib/site-packages --format json` reported
**188 known advisories across 18 installed packages**. The scanner did not provide
a uniform severity classification. `pip check` passed; dependency consistency is
not a vulnerability check. Cache-deserialisation warnings were emitted, but the
audit completed and produced advisory results. No shared packages were changed.

| Installed package | Version | Advisories | Highest advisory-listed candidate fix |
|---|---|---:|---|
| aiohttp | 3.14.1 | 14 | 3.14.4 |
| anyio | 4.13.0 | 2 | 4.14.2 |
| certifi | 2026.4.22 | 2 | 2026.10.18 |
| chromadb | 1.5.9 | 5 | None listed; remove/isolate or investigate |
| filelock | 3.29.5 | 9 | 4.2.1 |
| gitpython | 3.1.50 | 39 | 3.2.1 |
| msgpack | 1.2.1 | 1 | 1.2.3 |
| multidict | 6.7.1 | 1 | 6.9.1 |
| oauthlib | 3.3.1 | 3 | 4.0.1 |
| opentelemetry-api | 1.43.0 | 1 | 1.45.1 |
| pillow | 12.2.0 | 64 | 13.0.0 |
| pip | 24.0 | 12 | 26.2.0 |
| python-multipart | 0.0.28 | 6 | 0.0.31 |
| setuptools | 65.5.0 | 9 | 84.1.0 |
| soupsieve | 2.8.3 | 6 | 2.9.0 |
| starlette | 1.0.0 | 10 | 1.3.1 |
| torch | 2.12.1 | 1 | 2.13.0 |
| urllib3 | 2.7.0 | 3 | 2.8.0 |

These candidate versions have NOT been installed into the production environment
or verified as a compatible full-stack upgrade. ChromaDB includes unfixed findings.
Do not launch new execution against this shared environment. Triage and isolate
unneeded model, plugin and broker dependencies before a separate runtime cutover.

## Independent minimal research environment

`requirements-trading-research.txt` pins the already-tested numerical/API stack,
with patched multipart/Starlette/AnyIO/certifi requirements. The companion
`requirements-trading-research.lock` contains 38 fully resolved packages and
download hashes. It excludes torch, ChromaDB and broker SDKs.

`uvx pip-audit -r requirements-trading-research.lock --no-deps --disable-pip`
completed with **no known vulnerabilities found**. This is a point-in-time advisory
lookup, not a proof against unknown defects. Reproduction and test results for the
separate clean environment are in TEST_EVIDENCE.md and OPERATIONS.md.

## Frontend

Initial production-only npm audit: 4 affected packages (2 high, 2 moderate).
Updated existing axios to exact 1.20.0 and patched permitted transitive/build
dependencies in package-lock.json; no new top-level npm package was added.
Final full npm audit: **0 high, 0 critical, 2 moderate**. The two remaining findings
are react-router/react-router-dom; the suggested fix crosses to major v7.
A router migration was not silently bundled into this research feature.

The updated lock was installed with npm ci into a separate temporary frontend
copy; the shared dependency junction and main checkout dependencies were left
unchanged. Build and all four offline verifiers passed with this exact lock.

Advisory examples: [axios](https://github.com/advisories/GHSA-c29m-xwm3-cm6r),
[redirect credentials](https://github.com/advisories/GHSA-mrwp-f3q8-3g9p),
[router redirect](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6),
[router hydration](https://github.com/advisories/GHSA-337j-9hxr-rhxg).

## Scanning limits

Protected-path history check passed. `detect-secrets scan --no-verify` over current
tracked files reported 23 candidates in 10 unchanged files; zero candidates in
the feature's changed code. Candidate values were not printed or copied. These
baseline candidates remain for separate triage; they are not declared real secrets
or dismissed as false positives. No .env or vault content was copied into artifacts.
This is not a complete-history scan or credential validation; no claim of a clean
historical secret audit is made.
