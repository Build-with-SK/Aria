"""Bounded read-provider resilience, explicitly separate from executable quotes.

Wrap an injected read transport. No vendor credential lookup and no data-source
substitution. Broker order requests must never use this retry helper.
"""
from dataclasses import dataclass
import time


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadPolicy:
    attempts: int = 3
    backoff_seconds: float = 0.1
    minimum_interval_seconds: float = 1.0
    cache_seconds: float = 5.0

    def __post_init__(self):
        import math
        if not 1 <= self.attempts <= 4 or any(not math.isfinite(x) or x<0 for x in (
            self.backoff_seconds,self.minimum_interval_seconds,self.cache_seconds)):
            raise ValueError("bounded nonnegative read policy required")
        if self.backoff_seconds>2 or self.minimum_interval_seconds>60 or self.cache_seconds>60:
            raise ValueError("unbounded read delay/cache")


class ReadProvider:
    def __init__(self, transport, validate, *, policy=ReadPolicy(), clock=time.monotonic, sleep=time.sleep):
        import threading
        self.transport,self.validate=transport,validate
        self.policy,self.clock,self.sleep=policy,clock,sleep
        self._cache={}
        self._last=None
        self._healthy=None
        self._lock=threading.Lock()

    def read(self, key: str, *, executable: bool=False):
        # There is no 'force fresh' loophole for an executable quote cache.
        if executable:
            raise ProviderUnavailable("research read cache cannot supply executable broker quotes")
        with self._lock:
            cached=self._cache.get(key)
            if cached and self.clock()-cached[0]<self.policy.cache_seconds:
                return cached[1]
            for attempt in range(self.policy.attempts):
                if self._last is not None:
                    remaining=self.policy.minimum_interval_seconds-(self.clock()-self._last)
                    if remaining>0:
                        self.sleep(remaining)
                self._last=self.clock()
                try:
                    raw=self.transport(key)
                except (TimeoutError,ConnectionError,OSError):
                    self._healthy=False
                    if attempt+1<self.policy.attempts:
                        self.sleep(self.policy.backoff_seconds*2**attempt)
                        continue
                    raise ProviderUnavailable("read transport unavailable after bounded retries") from None
                # Schema errors fail immediately, not disguised as network retries.
                self._healthy=False
                value=self.validate(raw)
                self._healthy=True
                if len(self._cache)>=128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key]=(self.clock(),value)
                return value

    def health(self):
        return {"healthy":self._healthy,"cached_keys":len(self._cache),"scope":"research reads"}
