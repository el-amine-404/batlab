"""Load and abuse checks for one batlab app, from inside the sealed test network
(run.sh). Reads the app's own OpenAPI description, so it follows the API as it
is: every GET a page makes that needs no id.

  python load.py --base http://app:8830 --key KEY --out result.json

What it measures, at each concurrency level: requests per second, latency
(p50, p95, p99, max) and the answers that were not 2xx. Then three abuse
checks: many live streams held open while the app is asked for its health, a
body far too large, and wrong passwords in a row.
"""

import argparse
import asyncio
import json
import statistics
import time

import httpx

SKIP = ("events", "log", "archive", "stream", "files", "frames", "storyboard", "openapi", "/auth/")


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


async def endpoints(client: httpx.AsyncClient) -> list[str]:
    spec = (await client.get("/api/v1/openapi.json")).json()
    found = [path for path, ops in spec["paths"].items()
             if "get" in ops and "{" not in path and not any(word in path for word in SKIP)]
    # What a key may not read (people, keys: admins only), or what needs a
    # question in its address (…/check?path=), is no load the page makes.
    allowed = [path for path in found if (await client.get(path)).status_code < 400]
    return ["/", *sorted(set(allowed) | {"/api/v1/health"})]


async def level(client: httpx.AsyncClient, paths: list[str], workers: int, seconds: float) -> dict:
    latencies: list[float] = []
    by_path: dict[str, list[float]] = {path: [] for path in paths}
    statuses: dict[str, int] = {}
    stop = time.monotonic() + seconds

    async def worker(n: int):
        i = n
        while time.monotonic() < stop:
            path = paths[i % len(paths)]
            i += 1
            start = time.perf_counter()
            try:
                response = await client.get(path)
                code = str(response.status_code)
            except httpx.HTTPError as error:
                code = type(error).__name__
            took = (time.perf_counter() - start) * 1000
            latencies.append(took)
            by_path[path].append(took)
            statuses[code] = statuses.get(code, 0) + 1

    started = time.monotonic()
    await asyncio.gather(*(worker(n) for n in range(workers)))
    elapsed = time.monotonic() - started
    bad = sum(count for code, count in statuses.items() if not code.startswith(("2", "3")))
    return {
        "workers": workers,
        "requests": len(latencies),
        "rps": round(len(latencies) / elapsed, 1),
        "p50_ms": round(pct(latencies, 50), 1),
        "p95_ms": round(pct(latencies, 95), 1),
        "p99_ms": round(pct(latencies, 99), 1),
        "max_ms": round(max(latencies, default=0), 1),
        "not_ok": bad,
        "statuses": statuses,
        "slowest": sorted(((path, round(pct(v, 95), 1)) for path, v in by_path.items() if v),
                          key=lambda item: -item[1])[:5],
    }


async def streams(base: str, key: str, path: str, count: int) -> dict:
    """count live streams held open; meanwhile, how long health takes."""
    opened = 0
    async with httpx.AsyncClient(base_url=base, headers={"X-Api-Key": key},
                                 timeout=httpx.Timeout(10, read=None), limits=httpx.Limits(max_connections=count + 5)) as client:
        stack = []
        for _ in range(count):
            try:
                cm = client.stream("GET", path)
                response = await cm.__aenter__()
                if response.status_code == 200:
                    opened += 1
                stack.append(cm)
            except httpx.HTTPError:
                break
        health = []
        async with httpx.AsyncClient(base_url=base, timeout=10) as probe:
            for _ in range(10):
                start = time.perf_counter()
                try:
                    ok = (await probe.get("/api/v1/health")).status_code == 200
                except httpx.HTTPError:
                    ok = False
                health.append((time.perf_counter() - start) * 1000 if ok else None)
        for cm in stack:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - closing what may already be gone
                pass
    answered = [h for h in health if h is not None]
    return {"asked": count, "opened": opened, "health_answered": f"{len(answered)}/10",
            "health_median_ms": round(statistics.median(answered), 1) if answered else None}


async def abuse(base: str) -> dict:
    out = {}
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        body = b"x" * (64 * 1024 * 1024)
        start = time.perf_counter()
        try:
            response = await client.post("/api/v1/auth/login", content=body, headers={"Content-Type": "application/json"})
            out["body_64mb"] = {"status": response.status_code, "ms": round((time.perf_counter() - start) * 1000)}
        except httpx.HTTPError as error:
            out["body_64mb"] = {"error": type(error).__name__}
        codes = []
        for _ in range(15):
            response = await client.post("/api/v1/auth/login", json={"username": "nobody-here", "password": "wrong-password-1"})
            codes.append(response.status_code)
        out["wrong_logins"] = codes
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--levels", default="1,5,10,25,50,100")
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--sse", default="", help="a live stream's path, to hold many open")
    parser.add_argument("--sse-count", type=int, default=60)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result: dict = {"base": args.base}
    async with httpx.AsyncClient(base_url=args.base, headers={"X-Api-Key": args.key}, timeout=30,
                                 limits=httpx.Limits(max_connections=200, max_keepalive_connections=200)) as client:
        paths = await endpoints(client)
        result["paths"] = paths
        result["levels"] = []
        for workers in (int(n) for n in args.levels.split(",")):
            row = await level(client, paths, workers, args.seconds)
            result["levels"].append(row)
            print(f"{workers:>4} at once: {row['rps']:>7} req/s  p95 {row['p95_ms']:>7} ms  not ok {row['not_ok']}", flush=True)
    if args.sse:
        result["streams"] = await streams(args.base, args.key, args.sse, args.sse_count)
        print("streams:", result["streams"], flush=True)
    result["abuse"] = await abuse(args.base)
    print("abuse:", result["abuse"], flush=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
