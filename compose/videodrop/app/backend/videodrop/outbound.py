"""Requests videodrop makes to other apps (notifications/): one HTTP client,
one place for timeouts, the User-Agent and readable errors. The sources read
the communities through their own Fetcher (sources/fetch.py)."""

import urllib.parse

import httpx

USER_AGENT = "videodrop"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class SendFailed(Exception):
    """The message is shown to the user as is."""


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True,
                             limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


async def call(http: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """The answer to one request; any failure as SendFailed with a sentence."""
    try:
        response = await http.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise SendFailed(f"{host(url)} didn't answer in time.") from exc
    except httpx.HTTPError as exc:
        raise SendFailed(f"Couldn't reach {host(url)}: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("error") or body.get("message") or "")
        except ValueError:
            detail = response.text[:200] if "html" not in response.headers.get("content-type", "") else ""
        raise SendFailed(f"{host(url)} answered {response.status_code}: {(detail or response.reason_phrase)[:300]}.")
    return response
