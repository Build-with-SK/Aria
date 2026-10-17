"""SDK loop compatibility without connecting to a broker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from src.execution.ibkr_broker import _ensure_sdk_loop


def test_thread_without_default_loop_gets_one_and_existing_loop_is_preserved():
    def exercise():
        created = _ensure_sdk_loop()
        existing = asyncio.new_event_loop()
        try:
            assert _ensure_sdk_loop() is created
            asyncio.set_event_loop(existing)
            assert _ensure_sdk_loop() is existing
            async def running():
                assert _ensure_sdk_loop() is asyncio.get_running_loop()
            existing.run_until_complete(running())
        finally:
            created.close()
            existing.close()
            asyncio.set_event_loop(None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(exercise).result(timeout=5)
