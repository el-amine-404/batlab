# batlab-common

Code shared by batlab's own services, songdrop and musicguard. It's a plain
Python package with no dependencies beyond the standard library, so it also
runs inside spotDL's Python environment.

| Module | What it does |
| --- | --- |
| `titles` | Whether two titles and artists name one song (`agrees`), and which version a title names (`version`: remix, live, sped up…). |
| `fingerprint` | Chromaprint fingerprints (`fpcalc`, else ffmpeg) and how alike two are (`similarity`, `SAME`, `OTHER`). |
| `previews` | Spotify's and Apple's 30-second previews, and how much a file sounds like one (`score`). |
| `notify` | A message to Apprise, with a tag naming the channel (`send`). |
| `http` | GET with a size limit and a pause between requests to one host. |

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

- Standard library only. A dependency here becomes one in every image.
- Every function says in its docstring what it returns when it can't tell
  (`None`, never a guess).
- A change here is a change to every service: run their tests too
  (`compose/musicguard/tests`, `compose/music-links/songdrop/backend/tests`).
