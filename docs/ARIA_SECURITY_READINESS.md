# ARIA security readiness

Candidate base dependency audit PASS; deployed environment security remains BLOCKED. No blanket security waiver.

## Runtime migration prepared

Isolated Windows CPython **3.14.8**, standard GIL x64 build from Python's official full runtime ZIP. No installer, registry, PATH, system Python or production venv changed. Actual libraries: **Expat 2.8.5; OpenSSL 3.5.9**. Python release manifest and downloaded ZIP SHA-256 agree: `4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da`.

[Python 3.14.8 security release](https://www.python.org/downloads/release/python-3148/) and [Windows release manifest](https://www.python.org/ftp/python/3.14.8/windows-3.14.8.json) document these bundled upgrades. An initial uv catalogue lookup could not supply the requested patch; no uv/global updater was run. The official ZIP was hash-checked and extracted privately, then used to create a fresh app/test venv.

Reproduction: retrieve the manifest over verified HTTPS, select `pythoncore-3.14-64`, check the archive hash before extraction, extract to an empty private directory, run its `python.exe -m venv <candidate-venv>`, upgrade pip/setuptools there, install `requirements.txt` and `requirements-ci.txt`, run pip check/audit and complete regression. Keep the old runtime/state backup for owner-approved cutover. Never roll back a migrated trading snapshot to code that can ignore quarantine/UNKNOWN.

The old Windows 3.11.8 runtime remains unchanged (Expat 2.5.0/OpenSSL 3.0.13). Actual production migration is BLOCKED pending cutover approval. Linux compatibility ran on distro Python 3.14.4/Expat 2.7.4/OpenSSL 3.5.5; that run does not establish current distro security backports. Use a currently patched interpreter/library build before Linux deployment.

## Dependency and source findings

Fresh Windows:101 distributions, zero pip-audit findings. Fresh Linux:100, zero. npm:zero. pip check clean. Exact freezes and raw audits are private evidence. Constraints apply to installed packages; optional Chroma/Torch/OAuth stacks are excluded from this clean base and remain separately blocked for consumer-specific audit/remediation. Existing deployment packages were not upgraded. Router major is the security exception; unrelated Plotly/Anthropic major upgrades avoided, calendar pin retained.

Bandit medium/high scan exits1:49 patterns (45 medium,4 high):32 URL opens,6 dynamic SQL,5 XML,2 local pickle,4 SHA1. The extra URL-open site versus Phase2 is Claude's operator-supplied launch-check base URL, not a new public API fetch. SHA1 sites are identity/cache keys rather than authentication; SQL uses reviewed identifiers/bound values; local model/cache pickle must remain trusted; external XML requires the patched runtime. These are scoped assessments, not suppression or proof every data source is safe. Untrusted model code, Chroma HTTP server and arbitrary pickle ingestion remain prohibited deployment assumptions.

Anonymous audit of231 routes passes without warnings. Owner proof/step-up, service-token separation, live ceiling, kill switch, UNKNOWN session and reduce-only tests remain in force. The bounded queue timeout uses the existing shared-lock architecture; API responses now withhold host lock paths. Full security/static/type results and test limitations are in ARIA_FINAL_TEST_RESULTS.

No secrets, account identifiers, real symbols/order details or balances are included in this handoff. Changed tracked files are checked against configured secret values privately; bounded scanning cannot certify every possible historical secret. Protected-history fence remains mandatory.

Second-review hardening: damaged position/pending-cancel stores no longer become empty recovery books; management holds without sends/cancels. File fsync errors propagate, and POSIX replacements sync their parent directory. Storage failure after replacement may leave a valid new snapshot but cannot be reported as a successful durable transition.

Cutover compatibility: stop every old queue/desk/pipeline writer before opening real state with the candidate. Windows queue locking now reuses the shared lock byte range; mixed old/new queue writers are unsupported. Single-version downtime and preservation of new quarantine/audit schema are required. Do not copy an existing venv between paths/hosts; recreate it and audit the exact deployment.
