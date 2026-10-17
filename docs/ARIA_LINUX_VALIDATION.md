# Linux validation — Phase 2

Runtime protocol PASS on Ubuntu 26.04 LTS, WSL2 kernel
6.18.33.2-microsoft-standard-WSL2 x86_64, Python 3.14.4, non-root user.
This is actual Linux execution in an existing WSL distro with a **fresh venv
and isolated source snapshot**, not a newly provisioned VM or a production host.

The source was exported with `git archive` at b7b789c, without private .env,
runtime state, vault, or model files. The new POSIX runtime test was copied into
that temporary checkout. The distro lacks ensurepip; pip was bootstrapped from
the official PyPA get-pip script **inside the temporary venv only**. No apt,
sudo, system package, service, production process or credential was changed.

Full requirements.txt plus requirements-ci.txt installed successfully; pip check
reports no broken requirements. No optional Chroma/model packages were installed.
Python 3.14 selected NumPy 2.5.4 and SciPy 1.18.1; the Windows 3.11 resolver
selected 2.4.6 and 1.17.1. Calendar pin remains 4.13.2. This is why each platform
must run its own full suite instead of assuming one freeze works everywhere.

25 runtime/recovery tests passed in 2.91s. Evidence includes:

- Fresh backend child processes serving health/readiness and authenticated
  execution-queue reads on ephemeral loopback ports.
- Two process starts and SIGTERM shutdowns, with completed lifespan shutdown
  logs, followed by another process reading the same durable UNKNOWN claim.
  Uvicorn 0.54 restores/re-raises SIGTERM after graceful cleanup; its expected
  process exit is -15. The new test's initial assumption of exit 0 was corrected
  against installed server source and completed shutdown logs. Existing tests
  and assertions were not disabled or relaxed.
- Idempotent SQLite schema initialization, desk config save/load with automation
  disabled, paths containing spaces and Unicode, permission-denied queue writes
  preserving the prior snapshot, and POSIX lock/atomic-write code paths.
- Legacy crash/recovery tests and the existing real ASGI listener restart test.

Full Linux regression: **3408 passed, 21 skipped, 121.39s, exit 0** after
portability repairs. The first full run had 37 failures/3 errors: the installed
IBKR/eventkit SDK expected implicit event-loop creation removed in Python 3.14;
one scheduler test spoofed global os.name, changing pathlib's native flavor.
The adapter now explicitly owns a thread-local loop only when none exists;
existing/running loops are preserved, owned loops close at exit. No connection
is made by loop setup. The test now stubs only the scheduler module's OS view,
retaining all original assertions. Targeted portability rerun: 58 passed in
2.93s. Full source includes the candidate through 9862967.

The first attempted
runtime run overlapped unfinished dependency installation and is not a valid
runtime verdict; the recorded PASS above is after successful installation.

`.github/workflows/linux-runtime.yml` reproduces full dependency installation,
pip check, process/database/permissions checks, full suite and an exact-environment
audit with a separate auditor venv. Matrix: Python 3.12 (existing CI line) and
3.14 (locally tested Linux). This workflow is prepared locally and has **not**
been pushed or claimed as an executed GitHub Actions run. WSL validation does
not prove production daemon recovery, external brokers or host supervision.
