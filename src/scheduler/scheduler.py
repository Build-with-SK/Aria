"""
scheduler.py
============
Phase 4 — Scheduled automation.

Sets up Windows Task Scheduler to run main.py automatically every morning
before market open (default: 7:30 AM local time).

Also provides a lightweight built-in scheduler using Python's schedule
library for development/testing without Task Scheduler.

Usage:
  # Install the Windows Task Scheduler task (run once as admin)
  python src/scheduler/scheduler.py --install

  # Run in Python scheduler mode (development)
  python src/scheduler/scheduler.py --python-scheduler

  # Remove the scheduled task
  python src/scheduler/scheduler.py --uninstall

  # Test: run main.py immediately
  python src/scheduler/scheduler.py --run-now
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Resolve paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
PYTHON_EXE   = sys.executable
MAIN_PY      = PROJECT_ROOT / "main.py"
LOG_DIR      = PROJECT_ROOT / "data" / "logs"
TASK_NAME    = "TradingIntelligenceSystem"


def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def run_main(args: str = "") -> int:
    """
    Run main.py and capture output to a timestamped log file.
    Returns the process return code.
    """
    _ensure_log_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = LOG_DIR / f"run_{timestamp}.log"

    # No shell: the command is an argv list, so nothing in `args` can be
    # interpreted as a shell operator. The same pattern was fixed in the API's
    # pipeline runner; this scheduler path was still building a shell string.
    argv = [str(PYTHON_EXE), str(MAIN_PY)] + shlex.split(args or "")
    logger.info(f"Running: {argv}")
    logger.info(f"Log: {log_file}")

    with open(log_file, "w") as f:
        f.write(f"Run started: {datetime.now()}\n")
        f.write(f"Command: {argv}\n\n")
        f.flush()

        result = subprocess.run(
            argv, shell=False, cwd=str(PROJECT_ROOT),
            stdout=f, stderr=subprocess.STDOUT,
        )

        f.write(f"\nRun finished: {datetime.now()}\n")
        f.write(f"Return code: {result.returncode}\n")

    logger.info(f"Run complete. Return code: {result.returncode}")
    return result.returncode


# ===========================================================================
# Windows Task Scheduler
# ===========================================================================

def install_windows_task(
    run_time:    str  = "07:30",   # HH:MM 24-hour
    run_daily:   bool = True,
    no_sentiment: bool = False,
) -> bool:
    """
    Create a Windows Task Scheduler task to run main.py daily.

    Requires running PowerShell as Administrator once.
    After installation, the task runs automatically — no need to keep
    a terminal open.
    """
    if os.name != "nt":
        logger.error("Windows Task Scheduler is only available on Windows.")
        logger.info("On Linux/Mac, use cron: 30 7 * * 1-5 python /path/to/main.py")
        return False

    args = "--no-sentiment" if no_sentiment else ""
    cmd  = f'"{PYTHON_EXE}" "{MAIN_PY}" {args}'

    # PowerShell command to create the scheduled task
    ps_script = f"""
$Action  = New-ScheduledTaskAction -Execute '{PYTHON_EXE}' -Argument '"{MAIN_PY}" {args}' -WorkingDirectory '{PROJECT_ROOT}'
$Trigger = New-ScheduledTaskTrigger -Daily -At {run_time}
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force
Write-Host "Task '{TASK_NAME}' created. Will run daily at {run_time}."
"""

    ps_file = PROJECT_ROOT / "install_scheduler.ps1"
    ps_file.write_text(ps_script)

    logger.info("=" * 60)
    logger.info("Windows Task Scheduler Setup")
    logger.info("=" * 60)
    logger.info(f"Task name:  {TASK_NAME}")
    logger.info(f"Run time:   Daily at {run_time}")
    logger.info(f"Command:    {cmd}")
    logger.info("")
    logger.info("To install, run this in PowerShell as Administrator:")
    logger.info(f"  powershell -ExecutionPolicy Bypass -File \"{ps_file}\"")
    logger.info("")
    logger.info("Or open Task Scheduler manually and create a task with:")
    logger.info(f"  Program: {PYTHON_EXE}")
    logger.info(f"  Arguments: \"{MAIN_PY}\" {args}")
    logger.info(f"  Start in: {PROJECT_ROOT}")
    logger.info(f"  Trigger: Daily at {run_time}")

    return True


def uninstall_windows_task() -> bool:
    """Remove the scheduled task."""
    if os.name != "nt":
        return False

    ps_script = f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false"
    ps_file = PROJECT_ROOT / "uninstall_scheduler.ps1"
    ps_file.write_text(ps_script)

    logger.info(f"To remove the task, run in PowerShell as Administrator:")
    logger.info(f"  powershell -ExecutionPolicy Bypass -File \"{ps_file}\"")
    return True


# ===========================================================================
# Python-based scheduler (development / non-Windows)
# ===========================================================================

def run_python_scheduler(
    run_time: str  = "07:30",
    weekdays_only: bool = True,
    no_sentiment:  bool = False,
) -> None:
    """
    Lightweight Python scheduler using the schedule library.
    Runs in the foreground — keep the terminal open.

    For production on Windows, prefer install_windows_task() instead.
    """
    try:
        import schedule
    except ImportError:
        logger.error("schedule library not installed. Run: pip install schedule")
        sys.exit(1)

    args = "--no-sentiment" if no_sentiment else ""

    def job():
        now = datetime.now()
        if weekdays_only and now.weekday() >= 5:   # 5=Saturday, 6=Sunday
            logger.info("Weekend — skipping run")
            return
        logger.info(f"Scheduled run starting at {now.strftime('%Y-%m-%d %H:%M')}")
        run_main(args)

    schedule.every().day.at(run_time).do(job)

    logger.info("=" * 60)
    logger.info(f"Python Scheduler running. Next run: {run_time} (weekdays only: {weekdays_only})")
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 60)

    while True:
        schedule.run_pending()
        time.sleep(60)   # Check every minute


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Intelligence System Scheduler")
    parser.add_argument("--install",          action="store_true", help="Install Windows Task Scheduler task")
    parser.add_argument("--uninstall",        action="store_true", help="Remove scheduled task")
    parser.add_argument("--python-scheduler", action="store_true", help="Run Python scheduler (foreground)")
    parser.add_argument("--run-now",          action="store_true", help="Run main.py immediately")
    parser.add_argument("--time",             default="07:30",     help="Run time HH:MM (default 07:30)")
    parser.add_argument("--no-sentiment",     action="store_true", help="Skip sentiment in scheduled runs")

    args = parser.parse_args()

    if args.install:
        install_windows_task(run_time=args.time, no_sentiment=args.no_sentiment)

    elif args.uninstall:
        uninstall_windows_task()

    elif args.python_scheduler:
        run_python_scheduler(run_time=args.time, no_sentiment=args.no_sentiment)

    elif args.run_now:
        extra = "--no-sentiment" if args.no_sentiment else ""
        code  = run_main(extra)
        sys.exit(code)

    else:
        parser.print_help()
