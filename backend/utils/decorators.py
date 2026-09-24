"""
Reusable decorators: async retry-with-backoff and a simple token-bucket rate limiter.
"""
import asyncio
import functools
import time
from typing import Callable, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def async_retry(max_retries: int = 3, base_delay: float = 0.5, exceptions: tuple = (Exception,)):
    """Retry an async function with exponential backoff."""

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        func.__name__, attempt, max_retries, exc, delay,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
            logger.error("%s failed after %d attempts", func.__name__, max_retries)
            raise last_exc

        return wrapper

    return decorator


class RateLimiter:
    """Simple in-memory token-bucket rate limiter, safe for single-process use."""

    def __init__(self, max_calls: int, period_seconds: float):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self.period_seconds]
            if len(self._calls) >= self.max_calls:
                wait_time = self.period_seconds - (now - self._calls[0])
                if wait_time > 0:
                    logger.debug("Rate limit reached, sleeping %.2fs", wait_time)
                    await asyncio.sleep(wait_time)
            self._calls.append(time.monotonic())


def rate_limited(limiter: RateLimiter):
    """Decorator applying a shared RateLimiter instance to an async function."""

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            await limiter.acquire()
            return await func(*args, **kwargs)

        return wrapper

    return decorator
