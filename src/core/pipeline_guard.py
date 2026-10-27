"""One pipeline writer across scheduled, CLI and API entry points."""
from functools import wraps
from pathlib import Path

from .durable import SingleInstance

LOCK_FILE = Path(__file__).resolve().parents[2] / "data" / "pipeline.lock"


class PipelineAlreadyRunning(RuntimeError):
    pass


def busy():
    probe = SingleInstance(lambda: LOCK_FILE)
    try:
        return not probe.acquire()
    except OSError:
        return True  # unreadable locking state never permits another writer
    finally:
        probe.release()


def single_writer(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        lock = SingleInstance(lambda: LOCK_FILE)
        if not lock.acquire():
            raise PipelineAlreadyRunning("Another pipeline run holds the writer lock")
        try:
            return function(*args, **kwargs)
        finally:
            lock.release()
    return guarded
