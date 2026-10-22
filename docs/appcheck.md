# What the apps can take, and what was wrong

Security and load checks of subfeed, videodrop, clipseerr and songdrop, run with
[appcheck](../appcheck/README.md) on 2026-10-22: each app's own image, on a copy
of its data, on a Docker network with no way out (no site, YouTube included,
saw a single request). musicguard has no API: its code and dependencies were
checked (bandit, pip-audit), and its file handling read.

## How much they take

Answers a second on the reads a page makes, and the slowest 5 % (p95), with
the fixes below, on lab2, each app on its own:

| Clients at once | subfeed | videodrop | clipseerr | songdrop |
| --- | --- | --- | --- | --- |
| 1 | 227 /s, 9 ms | 163 /s, 28 ms | 160 /s, 18 ms | 234 /s, 7 ms |
| 5 | 327 /s, 28 ms | 272 /s, 46 ms | 226 /s, 46 ms | 354 /s, 25 ms |
| 10 | 316 /s, 66 ms | 259 /s, 87 ms | 225 /s, 90 ms | 273 /s, 124 ms |
| 25 | 226 /s, 333 ms | 188 /s, 386 ms | 186 /s, 381 ms | 250 /s, 300 ms |
| 100 | 232 /s, 1.2 s | 155 /s, 1.9 s | 173 /s, 1.7 s | 176 /s, 1.7 s |

What that means for people:

- **Each app is one Python process on one core**, and that core is the limit
  (memory stayed far under the containers' limits: subfeed used 50 MB of 256).
  The best is reached at 5 to 10 requests in flight; past that each one waits
  longer. With 8 threads instead of FastAPI's 40 (below), the rate holds
  instead of falling: 40 threads fought over the one core.
- **An open page asks ~0.1 to 0.3 times a second** (its lists, counts, status).
  Around 250 requests a second, every app answers in under 100 ms: **several
  hundred pages open at once**, far beyond a household. Opening a page is a
  burst of 10 to 20 requests: a few dozen people opening pages in the same
  second is where it slows down.
- **Downloads are the real limit, not the page**: yt-dlp, spotDL and ffmpeg
  use the CPU and the containers' memory (videodrop 2 GB, songdrop 1 GB), and
  YouTube limits the house's one address. Those weren't measured here: they
  need the internet, and testing them would hit YouTube.
- videodrop's live stream: 60 held open, the app still answered in 3 ms.
- Wrong passwords: refused after 5 in a row, from one address (429).

## Found and fixed

| | Where | What it was | Now |
| --- | --- | --- | --- |
| Security | all four | A 64 MB body to the open sign-in was read whole into memory before being refused; a few at once could take a container past its memory limit. | Nothing over 2 MB is read (`batlab_common.web.BodyLimit`); the largest the apps take is a 1 MB cookies.txt. |
| Security | subfeed, videodrop, clipseerr | certifi 2026.7.22 (PYSEC-2026-4320). | 2026.10.19. |
| Security | songdrop | Callbacks and webhooks were sent with urllib, which also opens file://; the addresses were already checked when given. | The sending side refuses anything but http(s) too. |
| Robustness | clipseerr | YouTube Music or Apple unreachable: a 500 ("something went wrong") on a video, an artist, Home, Search. | A 502 naming who didn't answer. |
| Robustness | videodrop | A number past SQLite's integers in an address or a question: a 500. | Refused (422). |
| Contract | all four | Every 422 was described as FastAPI's list of errors; the answers are `{"detail": "<sentence>"}`. A client made from the description read them wrong. | Described as they are. |
| Speed | clipseerr | Tags worked out every known video's tags on each call (~440 ms) and held every other request up: 45 answers a second, however many asked. | Kept a minute: 240 a second. |
| Speed | videodrop | /sites validated and encoded ~1800 sites on each call. | Made once a yt-dlp version, with an ETag. |
| Memory | clipseerr | Hot's artists had no bound. | The 2000 shown most recently. |
| Speed | all four | 40 threads for plain-def routes fought over one core: past 10 clients, answers a second fell (subfeed 324 → 136 at 100). | 8 (`batlab_common.web.THREADS`, `<APP>_THREADS`): measured at 4, 8, 16 and 40, 8 best in all four (subfeed 136 → 216, videodrop 120 → 177, songdrop 196 → 241 at 100). |
| Robustness | videodrop | A folder name past the disk's 255 characters: a 500. | Refused (422). |
| Contract | all four | The description left out answers every app gives (400, 401, 403, 404, 413, 429), a wrong method was a 404, the types and events a notification takes were "a string". | Written down; a wrong method is a 405 with Allow; types and events listed; empty lists and links refused by the description. |
| Contract | songdrop | Its callbacks weren't signed, and a job that ended well was "done" where videodrop says "finished". | Signed as videodrop's (`callback_secret`, `X-Songdrop-Signature`, `batlab_common.callbacks`); "finished", as the contract now says. |

## Checked and fine

- Sign-in: scrypt passwords, sessions HttpOnly and SameSite=Lax, a change
  made with the page's cookie must carry the app's own header (no other site
  can act as whoever is signed in), keys never in the logs.
- Download options are lists to choose from (`extra="forbid"`): nothing a
  person types reaches yt-dlp or spotDL as an argument. No shell anywhere;
  ffmpeg reads only the protocols it needs.
- musicguard runs every tool with a timeout, no stdin and no shell, on
  absolute paths.
- No server error left in fuzzing (clipseerr's 5 left are its 502s: the
  sources can't be reached from the sealed network).
- npm audit: nothing in the pages' dependencies.

## Still open

What fuzzing still reports is input the description allows and the app
refuses for a reason only the state knows: a subreddit or a destination that
must exist, a notification type's own required field ("Apprise address is
needed"), "an API key has no password", a cookies file's first line. Each is
a 422 saying why, which is right; the description can't say it. clipseerr's
remaining 5xx are its 502s (the sources can't be reached from the sealed
network). videodrop takes 0 and 1 for true and false in its download
options.

The download contract's two differences left are each service's own:
songdrop's progress is a number, videodrop's an object; videodrop streams its
changes (SSE), songdrop is asked.
