"""Reading a source over HTTP politely and safely: one call at a time per
source, spaced out (the sources limit by address), a timeout, a cap on how
much is read, and every failure as a SourceError with a sentence."""

import asyncio
import time

import httpx

from .base import BadAnswer, Refused, Unreachable

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# A page of 100 posts is well under 1 MB; more is not the source talking.
MAX_BYTES = 5 * 1024 * 1024


class Fetcher:
    def __init__(self, http: httpx.AsyncClient, user_agent: str, spacing: float):
        self._http = http
        self._headers = {"User-Agent": user_agent}
        self._spacing = spacing
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def get(self, url: str, params: dict | None = None) -> httpx.Response:
        async with self._lock:
            wait = self._last + self._spacing - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._read(url, params)
            finally:
                self._last = time.monotonic()

    async def _read(self, url: str, params: dict | None) -> httpx.Response:
        host = httpx.URL(url).host
        try:
            async with self._http.stream("GET", url, params=params, headers=self._headers, timeout=TIMEOUT,
                                         follow_redirects=True) as response:
                if response.status_code in (403, 429):
                    retry = response.headers.get("retry-after", "")
                    raise Refused(f"{host} answered {response.status_code}",
                                  float(retry) if retry.isdigit() else None)
                if response.status_code >= 400:
                    raise BadAnswer(f"{host} answered {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise BadAnswer(f"{host} sent more than {MAX_BYTES // (1024 * 1024)} MB")
        except httpx.TimeoutException as exc:
            raise Unreachable(f"{host} didn't answer in time") from exc
        except httpx.HTTPError as exc:
            raise Unreachable(f"couldn't reach {host} ({exc.__class__.__name__})") from exc
        return httpx.Response(response.status_code, headers=response.headers, content=bytes(body),
                              request=response.request)
