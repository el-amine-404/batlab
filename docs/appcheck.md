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
| 1 | 233 /s, 9 ms | 161 /s, 28 ms | 170 /s, 18 ms | 270 /s, 5 ms |
| 5 | 312 /s, 28 ms | 258 /s, 48 ms | 233 /s, 43 ms | 433 /s, 19 ms |
| 10 | 304 /s, 67 ms | 257 /s, 89 ms | 241 /s, 78 ms | 340 /s, 99 ms |
| 25 | 191 /s, 393 ms | 173 /s, 435 ms | 187 /s, 375 ms | 284 /s, 261 ms |
| 100 | 184 /s, 1.6 s | 148 /s, 2.0 s | 115 /s, 2.8 s | 246 /s, 1.1 s |

What that means for people:

- **Each app is one Python process on one core**, and that core is the limit
  (memory stayed far under the containers' limits: subfeed used 50 MB of 256).
  The best is reached at 5 to 10 requests in flight; past that each one waits
  longer and fewer are answered a second.
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

- **The API descriptions are looser than the APIs**: statuses answered but
  not described (400 for broken JSON, 403, 404), input the description allows
  but the app refuses (a rule only the code knows), 404 instead of 405 for a
  method a path doesn't have. Not a risk; it matters to whoever builds a
  client from the description.
- **The download contract** ([services.md](services.md#the-download-contract)):
  songdrop's callbacks aren't signed yet, and the two apps name a good end
  differently (`done` / `finished`).
- **Past ~10 requests in flight answers fall**, from threads competing for the
  one core. A smaller thread pool would hold the best rate longer; worth
  measuring before changing.
