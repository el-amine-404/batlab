# appcheck

Security and load checks of batlab's own web apps (subfeed, videodrop,
clipseerr, songdrop), on lab2, against throwaway copies. What they found and
the limits they measured: [docs/appcheck.md](../docs/appcheck.md).

```sh
appcheck/run.sh                       # all four
appcheck/run.sh clipseerr             # one
appcheck/run.sh static                # bandit, pip-audit, npm audit (musicguard too)
APPCHECK_IMAGE_CLIPSEERR=<image> appcheck/run.sh clipseerr   # an image not yet deployed
```

Results go to `appcheck/out/<date>/<app>/` (not kept in git): `load.txt` and
`load.json`, `schemathesis.txt`, `stats.txt` (memory and CPU every 2 s),
`app.log`, `end.txt` (killed for memory? restarted?).

## It stays inside

Nothing a check sends leaves lab2, whatever it is:

- Each copy runs the image the live app runs (or the one named), on a copy of
  its data in a temporary folder, removed after. The live app and its data are
  never touched.
- The copy and the tools share `appcheck-net`, an **internal** Docker
  network: no route out at all, no DNS. A job the fuzzer asks for, a
  callback, a webhook, YouTube, Apple, an address on the home network: each
  fails at once. No site sees the checks, so nothing can flag or block the
  house's address.
- Only `static` and building the tools' image reach out, to package indexes
  (PyPI, npm, their advisory databases): a handful of requests.
- The copy gets the live app's memory and process limits, so its limits are
  the real ones.

## What runs

| Check | Tool | Looks for |
| --- | --- | --- |
| Load | `load.py` | Answers a second and latency (p50, p95, p99) at 1 to 100 clients at once, on every GET the page makes that needs no id (from the app's OpenAPI). |
| Live streams | `load.py --sse` | 60 streams held open (videodrop): does the app still answer? |
| Abuse | `load.py` | A 64 MB body to the open sign-in; 15 wrong passwords in a row. |
| Fuzzing | schemathesis | Every operation of the OpenAPI, with valid and invalid input: 500s, answers that break the description, input wrongly taken or refused. |
| Code | bandit | Unsafe patterns (shells, temp files, weak hashes…). |
| Dependencies | pip-audit, npm audit | Known vulnerabilities in what the images install. |
