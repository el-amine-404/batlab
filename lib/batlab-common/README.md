# batlab-common

Code shared by batlab's own services: songdrop, musicguard, videodrop,
clipseerr and subfeed. It's a plain Python package with no dependencies
beyond the standard library (web.py aside, see Rules), so it also runs inside
spotDL's Python environment.

| Module | What it does |
| --- | --- |
| `titles` | Whether two titles and artists name one song (`agrees`), and which version a title names (`version`: remix, live, sped up…). |
| `fingerprint` | Chromaprint fingerprints (`fpcalc`, else ffmpeg) and how alike two are (`similarity`, `SAME`, `OTHER`). |
| `previews` | Spotify's and Apple's 30-second previews, and how much a file sounds like one (`score`). |
| `notify` | A message to Apprise, with a tag naming the channel (`send`). |
| `http` | GET with a size limit and a pause between requests to one host. |
| `accounts` | Each app's people and API keys: scrypt passwords, sessions, keys with permissions, sign-in throttling, the setup code that makes the first admin. The tables live in the app's SQLite database (or `Database`, a file of their own). |
| `web` | The same for a FastAPI app: `Gate` says who asks (session cookie or `X-Api-Key`), `need()` checks a permission, and it brings the routes for signing in, one's account, people and keys. |
| `admin` | `python -m <app>.admin`: the setup code, the people, a new password, a key, from the server's shell. |
| `netnames` | What an address is called on the home network (`homepage`, `pixel-7`), for "used by…". |
| `egress` | The proxy that lets an app's link downloads reach the internet and nowhere else (videodrop, songdrop). |

## How the services get it

The images copy `batlab_common/` in and put it on `PYTHONPATH`; compose hands
the folder to the build as the named context `common`
(`additional_contexts`). Nothing is installed from a package index.

To work on it locally:

```sh
cd lib/batlab-common
python3 -m unittest discover -s tests     # needs ffmpeg for the fingerprint tests
pip install -e .                          # if you want it importable elsewhere
```

## Rules

- Standard library only. A dependency here becomes one in every image. The one
  exception is `web`, which needs FastAPI: only the web servers import it,
  never spotDL's environment.
- Every function says in its docstring what it returns when it can't tell
  (`None`, never a guess).
- A change here is a change to every service: run their tests too
  (`compose/musicguard/tests`, `compose/songdrop/app/backend/tests`,
  and those of videodrop, clipseerr and subfeed). Each web app's image build
  runs `tests/test_accounts.py` and `tests/test_egress.py` against its own
  FastAPI: a failure stops its deploy.
