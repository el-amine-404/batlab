# How songdrop is built

What a new contributor needs to change songdrop with confidence. The rules all
batlab services follow (one job, an API before a page, events instead of
recipients) are in `docs/services.md` in the repository.

## The parts

```
compose/songdrop/app/
├── backend/            Python 3, FastAPI: the API, jobs, runners
│   ├── app/
│   │   ├── main.py         the web server: routers, error handling, the page and the docs
│   │   ├── context.py      the one settings / connections / job manager, made at start
│   │   ├── routes/         the API, one module per area (jobs, settings, folders, connections, system)
│   │   ├── schemas.py      what the API answers, as models: the OpenAPI description comes from them
│   │   ├── jobs.py         JobManager: the queue, workers, later tries, events
│   │   ├── runners/        spotDL and yt-dlp, each run as a child process per job
│   │   ├── connections/    where events go: one module per type
│   │   ├── events.py       the events songdrop reports
│   │   ├── preferences.py  the settings the API can change, kept in settings.json
│   │   ├── folders.py      the folders under the output root
│   │   └── system.py       tool versions and disk space
│   └── tests/
├── frontend/           React, TypeScript, Tailwind, shadcn/ui-style components
│   └── src/
│       ├── app/navigation.tsx  every screen: the sidebar's, then the settings'; the router is made from it
│       ├── pages/              one component per screen
│       ├── components/         what screens are made of (jobs/, settings/, connections/, layout/, ui/)
│       └── lib/                the API client (api.ts), its generated types (schema.d.ts), hooks
└── docs/               this site (VitePress, on GitHub Pages); its API reference is Scalar on a copy of the API's description
```

The page is a client of the API like any other app: nothing it does is out of
reach of the API.

## Run it

Tests run in songdrop's image, which has spotDL, yt-dlp and ffmpeg:

```bash
docker run --rm --network none --entrypoint /opt/songdrop/.venv/bin/python \
  -v "$PWD/backend:/w" -w /w <songdrop image> -m unittest discover -s tests
```

The page against a running songdrop:

```bash
cd frontend && npm install && SONGDROP_API=http://songdrop.homelab.lan npm run dev
```

The docs: `cd docs && npm install && npm run dev`. They are published to
https://el-amine-404.github.io/batlab/songdrop/ by `.github/workflows/docs.yml`
when a change to them reaches `main`. After a change to the API, refresh the
reference's copy of its description from a songdrop running it:
`SONGDROP_OPENAPI=http://songdrop.homelab.lan/api/v1/openapi.json npm run openapi`.

## Common changes

### A new setting

1. `backend/app/config.py`: the field and its environment variable.
2. `backend/app/preferences.py`: the field in `SettingsPatch` (with its limits),
   in `apply()`, and in `public()`.
3. `backend/app/schemas.py`: the field in `Settings`.
4. `cd frontend && SONGDROP_OPENAPI=<openapi.json or URL> npm run gen:api`:
   the TypeScript types follow.
5. The settings screen that shows it (`frontend/src/pages/settings/`): add its key
   to the screen's `KEYS` and a row. Save, Discard and the unsaved-changes guard
   come with `useSettingsForm`.

### A new screen

A component in `frontend/src/pages/`, and one entry in
`frontend/src/app/navigation.tsx`. The route and the sidebar link follow (or
the settings' list, for a setting). The sidebar and the frame themselves are
batlab's, shared with its other apps: `lib/batlab-ui`.

### A new connection type

One module in `backend/app/connections/` with a `ConnectionType` subclass: its
`fields` (the form), `validate()`, `send()` and `summary()`; then add it to
`TYPES` in `connections/__init__.py`. The Connect screen draws its form from
the API, so the page needs no change (an icon in
`frontend/src/components/connections/icons.ts` is optional).

### A new event

Add it to `CATALOG` in `backend/app/events.py`, and emit it where it happens
(`self._emit(Event(...))` in `jobs.py`). Connections can tick it at once.

## Conventions

- **Errors are sentences.** A validator raises `ValueError("<sentence>")`; the API
  answers `{"detail": "<sentence>"}`. An exception meant for the user is listed in
  `main.py` with its status.
- **Secrets are write-only.** The API says whether one is set, never what it is.
- **Files songdrop writes are written beside, then moved in**, so a reader never
  sees half a file. Files holding secrets are mode 600.
- **No new dependency without a reason**, and every version is pinned.
