import time
from urllib.parse import urlparse

import httpx


def get_json(url: str, params: dict | None = None, delay: float = 0.1, tries: int = 4) -> dict | list:
    """Polite GET. Retries 429/5xx/transport errors; {} on 404. Errors name the path only, never the query."""
    r = _get(url, params, delay, tries)
    return {} if r is None else r.json()


def get_bytes(url: str, delay: float = 0.1, tries: int = 4) -> bytes | None:
    """Same polite GET for binary bodies; None on 404."""
    r = _get(url, None, delay, tries)
    return None if r is None else r.content


def _get(url: str, params: dict | None, delay: float, tries: int):
    path = urlparse(url).path
    time.sleep(delay)
    for i in range(tries):
        try:
            r = httpx.get(url, params=params, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code != 429 and r.status_code < 500:
                r.raise_for_status()
                return r
            why = f"HTTP {r.status_code}"
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"GET {path} failed: HTTP {e.response.status_code}") from None
        except httpx.TransportError as e:
            why = type(e).__name__
        if i < tries - 1:
            time.sleep(delay + 2**i)
    raise RuntimeError(f"GET {path} failed after {tries} tries: {why}")
