"""Record repeatable isolated release checks, exact source and every JUnit skip.

Run from an integration worktree using its intended Python environment. This
does not start production services, contact brokers, install dependencies or
train/promote models. Browser checks use the existing synthetic test fixtures.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def source():
    files = git("ls-files", "-z").split("\0")
    files += git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    digest = hashlib.sha256()
    for name in sorted(set(files)):
        if not name or name.startswith(("data/", "models/", "training/runs/")):
            continue
        path = ROOT / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {"commit": git("rev-parse", "HEAD"),
            "status": git("status", "--short"), "file_sha256": digest.hexdigest()}


def junit(path):
    if not path.exists():
        return {"missing_report": True}
    tree = ET.parse(path)
    cases = list(tree.iter("testcase"))
    exceptional = []
    for case in cases:
        for kind in ("failure", "error", "skipped"):
            event = case.find(kind)
            if event is not None:
                exceptional.append({"test": case.get("classname", "") + "::" + case.get("name", ""),
                                    "outcome": kind, "reason": event.get("message", ""),
                                    "detail": event.text or ""})
    return {"tests": len(cases), "passed": len(cases) - len(exceptional),
            "exceptional": exceptional}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("aria", "sentinel"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory outside the repository")
    parser.add_argument("--checks", nargs="+", choices=("inventory", "dependencies", "full", "browser", "frontend"),
                        default=["inventory", "dependencies", "full"])
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("Evidence must be outside the source checkout")
    # Never overwrite prior evidence, and never use a production data location.
    if any(p.name.lower() == "data" for p in (out, *out.parents)):
        parser.error("Evidence must not live under a data directory")
    out.mkdir(parents=True, exist_ok=False)
    before = source()
    report = {"product": args.product, "started_utc": datetime.now(timezone.utc).isoformat(),
              "source_before": before, "python": sys.version, "executable": sys.executable,
              "openssl": ssl.OPENSSL_VERSION,
              "packages": sorted({(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()}),
              "checks": [], "acceptance": "INCOMPLETE: external/review/model gates are separate"}
    plan = []
    if "dependencies" in args.checks:
        plan.append(("pip-check", [sys.executable, "-m", "pip", "check"], ROOT, {}))
    if "frontend" in args.checks:
        if args.product != "aria":
            parser.error("SENTINEL UI checks are in the browser preset")
        npm = shutil.which("npm")
        node = shutil.which("node")
        if not npm or not node:
            parser.error("Node/npm unavailable")
        for script in ("verify-launch-routes.mjs", "verify-technical-dashboard.mjs", "verify-execution-status.mjs"):
            plan.append((script, [node, "scripts/" + script], ROOT / "frontend", {}))
        plan.append(("frontend-build", [npm, "run", "build", "--", "--outDir", str(out / "dist")], ROOT / "frontend", {}))
        plan.append(("verify-shell", [node, "scripts/verify-shell.mjs"], ROOT / "frontend", {}))
    if "full" in args.checks:
        command = [sys.executable, "-m", "pytest", "tests", "-q", "-ra", "--junitxml=" + str(out / "full.xml")]
        env = {}
        if args.product == "aria":
            command += ["-p", "no:randomly"]
            if "frontend" in args.checks:
                env["ARIA_ACCEPTANCE_DIST"] = str(out / "dist")
        plan.append(("full", command, ROOT, env))
    if "browser" in args.checks:
        if args.product != "sentinel":
            parser.error("ARIA browser acceptance requires its documented served fixture")
        command = [sys.executable, "-m", "pytest", "tests/test_dynamic_browser.py", "tests/test_unified_ui.py",
                   "tests/test_automation_ui.py", "tests/test_unified_live_smoke.py", "-q", "-ra",
                   "--junitxml=" + str(out / "browser.xml")]
        plan.append(("browser", command, ROOT, {"SENTINEL_AUTOMATION_SMOKE": "1"}))
    def save():
        (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    save()
    try:
        for name, command, cwd, overrides in plan:
            start = time.monotonic()
            entry = {"name": name, "command": command, "cwd": str(cwd), "environment_overrides": overrides}
            report["checks"].append(entry)
            save()
            print("Running " + name, flush=True)
            try:
                with (out / (name + ".log")).open("w", encoding="utf-8") as log:
                    result = subprocess.run(command, cwd=cwd, env={**os.environ, **overrides, "PYTHONUTF8": "1"},
                                            stdout=log, stderr=subprocess.STDOUT, timeout=1800)
                entry["exit_code"] = result.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                entry.update(exit_code=-1, error=str(exc))
            entry["seconds"] = round(time.monotonic() - start, 3)
            if name in ("full", "browser"):
                entry["junit"] = junit(out / (name + ".xml"))
                if entry["junit"].get("missing_report"):
                    entry["report_incomplete"] = True
            save()
    finally:
        report["source_after"] = source()
        report["source_unchanged"] = report["source_after"] == before
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    failed = not report["source_unchanged"] or any(c.get("exit_code") != 0 or c.get("report_incomplete") for c in report["checks"])
    print(str(out / "verification.json"), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
