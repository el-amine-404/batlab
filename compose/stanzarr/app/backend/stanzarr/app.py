"""The web application: API, background work, and the built interface at /."""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, auth, language, library, lyricsfiles, operations, peaks
from .config import Config
from .db import Database

log = logging.getLogger("stanzarr")

ONLINE_SECONDS = 60  # a worker that checked in within this is online
STALE_SECONDS = 300  # a running job whose worker went quiet this long is queued again
SEARCH_BATCH = 200  # LRCLIB questions per pass, so a big first scan stays polite
MAX_UPLOAD = 80 * 1024 * 1024


class Background:
    """Scan, then ask LRCLIB, then tidy the queue; every scan_interval, or now when woken."""

    def __init__(self, db: Database, cfg: Config):
        self.db, self.cfg = db, cfg
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.busy = ""
        self.last_scan: dict = {}
        self.thread = threading.Thread(target=self.run, name="stanzarr-background", daemon=True)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.busy = "scanning"
                self.last_scan = library.scan(self.db, self.cfg.library) | {"at": int(time.time())}
                if self.cfg.lrclib and self.db.setting("lrclib", "1") == "1":
                    self.busy = "searching LRCLIB"
                    self.search_pass()
                self.busy = ""
                self.requeue_stale()
            except Exception:  # keep going: one bad file or a network outage is not fatal
                log.exception("background pass failed")
                self.busy = ""
            self.wake.wait(self.cfg.scan_interval)
            self.wake.clear()

    def search_pass(self) -> None:
        candidates = self.db.all(
            "SELECT s.id FROM songs s LEFT JOIN attempts a ON a.song_id=s.id"
            " WHERE NOT EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.source!='ai')"
            " AND (a.at IS NULL OR a.at < ?) ORDER BY a.at IS NOT NULL, a.at LIMIT ?",
            (int(time.time()) - self.cfg.retry_days * 86400, SEARCH_BATCH))
        for row in candidates:
            if self.stop.is_set():
                return
            try:
                operations.search_lrclib(self.db, self.cfg, row["id"])
            except Exception as error:  # LRCLIB down: try again next pass
                log.warning("LRCLIB search failed for song %s: %s", row["id"], error)
                return

    def requeue_stale(self) -> None:
        cutoff = int(time.time()) - STALE_SECONDS
        self.db.run("UPDATE jobs SET status='queued', worker='', stage='' WHERE status='running' AND worker NOT IN"
                    " (SELECT name FROM workers WHERE last_seen >= ?)", (cutoff,))


def create_app(cfg: Config | None = None, background: bool = True) -> FastAPI:
    cfg = cfg or Config()
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    if cfg.username and cfg.password:
        auth.create_user(db, cfg.username, cfg.password)
    worker = Background(db, cfg)
    limiter = auth.LoginLimiter()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if background:
            worker.thread.start()
        yield
        worker.stop.set()
        worker.wake.set()

    app = FastAPI(title="Stanzarr", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.db, app.state.cfg, app.state.background = db, cfg, worker

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
                and not request.url.path.startswith("/api/worker/"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return JSONResponse({"detail": "cross-site request refused"}, 403)
            if request.headers.get("content-length", "0") != "0" and \
                    not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "JSON only"}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline';"
            " font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        return response

    # --- people -------------------------------------------------------------------------

    def user(request: Request) -> dict:
        found = auth.session_user(db, request.cookies.get(auth.COOKIE))
        if not found:
            raise HTTPException(401, "sign in first")
        return found

    def worker_auth(request: Request) -> str:
        key = request.headers.get("x-stanzarr-key", "")
        if not key or not auth.hmac.compare_digest(key, auth.worker_key(db, cfg.worker_key)):
            raise HTTPException(401, "bad worker key")
        return request.headers.get("x-stanzarr-worker", "worker")[:64]

    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/session")
    def session(request: Request):
        found = auth.session_user(db, request.cookies.get(auth.COOKIE))
        return {"user": found["username"] if found else None, "setup": not auth.has_users(db), "version": __version__}

    def _set_cookie(response: Response, token: str) -> None:
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="strict",
                            secure=cfg.cookie_secure, path="/")

    @app.post("/api/setup")
    def setup(body: dict, response: Response):
        if auth.has_users(db):
            raise HTTPException(409, "already set up")
        username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
        if not username or len(password) < 8:
            raise HTTPException(422, "a username and a password of 8 characters or more")
        auth.create_user(db, username, password)
        _set_cookie(response, auth.login(db, username, password) or "")
        db.event("auth", f"Account {username} created")
        return {"user": username}

    @app.post("/api/login")
    def login(body: dict, request: Request, response: Response):
        address = request.client.host if request.client else "?"
        if limiter.blocked(address):
            raise HTTPException(429, "too many attempts, wait a few minutes")
        token = auth.login(db, str(body.get("username", "")), str(body.get("password", "")))
        if not token:
            limiter.failed(address)
            raise HTTPException(401, "wrong username or password")
        limiter.succeeded(address)
        _set_cookie(response, token)
        return {"user": body.get("username")}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        auth.logout(db, request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/password")
    def password(body: dict, me: dict = Depends(user)):
        row = db.one("SELECT password FROM users WHERE id=?", (me["id"],))
        if not row or not auth.check_password(str(body.get("current", "")), row["password"]):
            raise HTTPException(401, "the current password is wrong")
        new = str(body.get("new", ""))
        if len(new) < 8:
            raise HTTPException(422, "8 characters or more")
        auth.create_user(db, me["username"], new)
        return {"ok": True}

    # --- overview -----------------------------------------------------------------------

    def workers_online() -> list[dict]:
        return db.all("SELECT * FROM workers WHERE last_seen >= ? ORDER BY last_seen DESC",
                      (int(time.time()) - ONLINE_SECONDS,))

    @app.get("/api/status")
    def status(me: dict = Depends(user)):
        online = workers_online()
        return {"version": __version__, "user": me["username"], "read_only": cfg.read_only,
                "background": worker.busy, "last_scan": worker.last_scan,
                "workers_online": len(online), "gpu_online": any(w["gpu"] for w in online),
                "lrclib": cfg.lrclib and db.setting("lrclib", "1") == "1"}

    @app.get("/api/stats")
    def stats(me: dict = Depends(user)):
        by_status = {r["status"]: r["n"] for r in db.all(
            f"SELECT {library.STATUS_SQL} AS status, COUNT(*) AS n FROM songs s GROUP BY status")}
        by_language = db.all(
            "SELECT language, COUNT(DISTINCT song_id) AS n FROM lyrics GROUP BY language ORDER BY n DESC")
        by_format = db.all("SELECT format, synced, COUNT(*) AS n FROM lyrics GROUP BY format, synced ORDER BY n DESC")
        to_check = db.one("SELECT COALESCE(SUM(unsure),0) AS lines, COUNT(*) AS songs FROM lyrics"
                          " WHERE source='ai' AND ai_state='unchecked'")
        return {"songs": sum(by_status.values()), "by_status": {k: by_status.get(k, 0) for k in library.STATUSES},
                "by_language": [{"code": r["language"], "name": language.name(r["language"]), "songs": r["n"]}
                                for r in by_language],
                "by_format": by_format, "ai_to_check": to_check,
                "events": db.all("SELECT * FROM events ORDER BY id DESC LIMIT 15")}

    # --- songs --------------------------------------------------------------------------

    @app.get("/api/songs")
    def songs(status: str = "", q: str = "", language_code: str = "", format: str = "", page: int = 1,
              size: int = 50, me: dict = Depends(user)):
        size = max(1, min(200, size))
        where, args = [], []
        if status:
            where.append(f"({library.STATUS_SQL}) = ?")
            args.append(status)
        if q:
            where.append("(s.title LIKE ? OR s.artist LIKE ? OR s.album LIKE ?)")
            args += [f"%{q}%"] * 3
        if language_code:
            where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.language=?)")
            args.append(language_code)
        if format:
            where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.format=?)")
            args.append(format)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = db.one(f"SELECT COUNT(*) AS n FROM songs s {clause}", args)["n"]
        rows = db.all(
            f"SELECT s.id, s.artist, s.album, s.title, s.duration, s.rel, s.root, {library.STATUS_SQL} AS status,"
            " (SELECT GROUP_CONCAT(DISTINCT l.format) FROM lyrics l WHERE l.song_id=s.id) AS formats,"
            " (SELECT GROUP_CONCAT(DISTINCT l.language) FROM lyrics l WHERE l.song_id=s.id AND l.language!='') AS languages,"
            " (SELECT MAX(l.unsure - l.checked) FROM lyrics l WHERE l.song_id=s.id AND l.source='ai') AS to_check,"
            " (SELECT j.status FROM jobs j WHERE j.song_id=s.id ORDER BY j.id DESC LIMIT 1) AS job"
            f" FROM songs s {clause} ORDER BY s.artist COLLATE NOCASE, s.album COLLATE NOCASE, s.rel"
            " LIMIT ? OFFSET ?", args + [size, (max(1, page) - 1) * size])
        for r in rows:
            r["formats"] = sorted((r["formats"] or "").split(",")) if r["formats"] else []
            r["languages"] = [{"code": c, "name": language.name(c)} for c in (r["languages"] or "").split(",") if c]
        return {"total": total, "page": page, "size": size, "songs": rows}

    def _song(song_id: int) -> dict:
        found = operations.song_or_none(db, song_id)
        if not found:
            raise HTTPException(404, "no such song")
        return found

    @app.get("/api/songs/{song_id}")
    def song(song_id: int, me: dict = Depends(user)):
        found = _song(song_id)
        files = db.all("SELECT * FROM lyrics WHERE song_id=? ORDER BY synced DESC, format", (song_id,))
        for f in files:
            f["language_name"] = language.name(f["language"])
            f["name"] = "embedded in the audio file" if f["path"].startswith("embedded:") else os.path.basename(f["path"])
        status_row = db.one(f"SELECT {library.STATUS_SQL} AS status FROM songs s WHERE s.id=?", (song_id,))
        return {"song": found | {"status": status_row["status"] if status_row else "missing"}, "files": files,
                "attempt": db.one("SELECT * FROM attempts WHERE song_id=?", (song_id,)),
                "jobs": db.all("SELECT * FROM jobs WHERE song_id=? ORDER BY id DESC LIMIT 5", (song_id,)),
                "events": db.all("SELECT * FROM events WHERE song_id=? ORDER BY id DESC LIMIT 10", (song_id,)),
                "has_voice": os.path.exists(os.path.join(cfg.artifacts_dir, str(song_id), "vocals.ogg")),
                "has_text": operations.best_text(db, song_id) is not None}

    @app.post("/api/songs/{song_id}/search")
    def search(song_id: int, me: dict = Depends(user)):
        _song(song_id)
        try:
            return {"result": operations.search_lrclib(db, cfg, song_id)}
        except Exception as error:
            raise HTTPException(502, f"LRCLIB did not answer: {error}")

    @app.post("/api/songs/{song_id}/ai-sync")
    def ai_sync(song_id: int, me: dict = Depends(user)):
        found = _song(song_id)
        if not any(w["gpu"] for w in workers_online()):
            raise HTTPException(409, "no GPU worker is online")
        if operations.best_text(db, song_id) is None:
            raise HTTPException(409, "AI sync needs the words first: no lyrics to place")
        if db.one("SELECT 1 FROM jobs WHERE song_id=? AND status IN ('queued','running')", (song_id,)):
            raise HTTPException(409, "already queued")
        job = db.run("INSERT INTO jobs(song_id, kind, status, created_at) VALUES(?, 'ai_sync', 'queued', ?)",
                     (song_id, int(time.time())))
        db.event("ai", f"{found['artist']} – {found['title']}: AI sync queued", song_id)
        return {"job": job}

    @app.get("/api/songs/{song_id}/editor")
    def editor(song_id: int, me: dict = Depends(user)):
        found = _song(song_id)
        data = operations.editor_lines(db, song_id)
        return data | {"song": {k: found[k] for k in ("id", "artist", "album", "title", "duration", "rel", "root")},
                       "has_voice": os.path.exists(os.path.join(cfg.artifacts_dir, str(song_id), "vocals.ogg")),
                       "read_only": cfg.read_only}

    @app.put("/api/songs/{song_id}/lyrics")
    def save(song_id: int, body: dict, me: dict = Depends(user)):
        _song(song_id)
        lines = body.get("lines")
        if not isinstance(lines, list) or len(lines) > 5000:
            raise HTTPException(422, "lines expected")
        try:
            return {"saved": operations.save_from_editor(db, cfg, song_id, lines)}
        except operations.ReadOnly:
            raise HTTPException(409, "Stanzarr is read-only")

    def _voice(song_id: int) -> str:
        return os.path.join(cfg.artifacts_dir, str(song_id), "vocals.ogg")

    @app.get("/api/songs/{song_id}/audio/{kind}")
    def audio(song_id: int, kind: str, me: dict = Depends(user)):
        found = _song(song_id)
        path = _voice(song_id) if kind == "voice" else found["path"]
        if kind not in ("mix", "voice") or not os.path.exists(path):
            raise HTTPException(404, "no such track")
        return FileResponse(path)

    @app.get("/api/songs/{song_id}/peaks/{kind}")
    def song_peaks(song_id: int, kind: str, me: dict = Depends(user)):
        found = _song(song_id)
        path = _voice(song_id) if kind == "voice" else found["path"]
        if kind not in ("mix", "voice") or not os.path.exists(path):
            raise HTTPException(404, "no such track")
        key = f"{song_id}-{kind}-{int(os.path.getmtime(path))}"
        return Response(peaks.cached(cfg.cache_dir, key, path), media_type="application/json")

    # --- the queue and the workers ------------------------------------------------------

    @app.get("/api/jobs")
    def jobs(me: dict = Depends(user)):
        return db.all("SELECT j.*, s.artist, s.title FROM jobs j JOIN songs s ON s.id=j.song_id"
                      " ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, j.id DESC LIMIT 200")

    @app.delete("/api/jobs/{job_id}")
    def cancel(job_id: int, me: dict = Depends(user)):
        db.run("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')",
               (int(time.time()), job_id))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: int, me: dict = Depends(user)):
        db.run("UPDATE jobs SET status='queued', worker='', stage='', error='' WHERE id=? AND status IN"
               " ('failed','cancelled')", (job_id,))
        return {"ok": True}

    @app.get("/api/workers")
    def workers(me: dict = Depends(user)):
        rows = db.all("SELECT * FROM workers ORDER BY last_seen DESC")
        now = int(time.time())
        for r in rows:
            r["online"] = now - r["last_seen"] < ONLINE_SECONDS
        return {"workers": rows, "key": auth.worker_key(db, cfg.worker_key), "key_fixed": bool(cfg.worker_key)}

    @app.post("/api/workers/key")
    def new_key(me: dict = Depends(user)):
        if cfg.worker_key:
            raise HTTPException(409, "the key is set by STANZARR_WORKER_KEY")
        return {"key": auth.regenerate_worker_key(db)}

    @app.post("/api/worker/heartbeat")
    def heartbeat(body: dict, name: str = Depends(worker_auth)):
        db.run("INSERT INTO workers(name, gpu, vram, version, last_seen) VALUES(?, ?, ?, ?, ?)"
               " ON CONFLICT(name) DO UPDATE SET gpu=excluded.gpu, vram=excluded.vram, version=excluded.version,"
               " last_seen=excluded.last_seen",
               (name, str(body.get("gpu", ""))[:120], int(body.get("vram", 0) or 0), str(body.get("version", ""))[:32],
                int(time.time())))
        return {"ok": True}

    @app.post("/api/worker/next")
    def next_job(name: str = Depends(worker_auth)):
        with db.tx() as conn:
            row = conn.execute("SELECT id, song_id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return Response(status_code=204)
            conn.execute("UPDATE jobs SET status='running', worker=?, started_at=?, stage='starting' WHERE id=?",
                         (name, int(time.time()), row["id"]))
        found = _song(row["song_id"])
        lyrics = operations.best_text(db, row["song_id"])
        words = [l.s for l in (lyrics.lines if lyrics else []) if l.s.strip()
                 and not (l.s.startswith("[") and l.s.endswith("]"))]
        return {"job": row["id"], "song": {k: found[k] for k in ("artist", "title", "album", "duration")},
                "lines": words, "audio_name": os.path.basename(found["path"])}

    def _running(job_id: int, name: str) -> dict:
        job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or job["status"] != "running" or job["worker"] != name:
            raise HTTPException(409, "not your job, or no longer running")
        return job

    @app.get("/api/worker/jobs/{job_id}/audio")
    def job_audio(job_id: int, name: str = Depends(worker_auth)):
        job = _running(job_id, name)
        return FileResponse(_song(job["song_id"])["path"])

    @app.post("/api/worker/jobs/{job_id}/stage")
    def job_stage(job_id: int, body: dict, name: str = Depends(worker_auth)):
        _running(job_id, name)
        db.run("UPDATE jobs SET stage=? WHERE id=?", (str(body.get("stage", ""))[:80], job_id))
        return {"ok": True}

    @app.put("/api/worker/jobs/{job_id}/voice")
    async def job_voice(job_id: int, request: Request, name: str = Depends(worker_auth)):
        job = _running(job_id, name)
        if int(request.headers.get("content-length", "0")) > MAX_UPLOAD:
            raise HTTPException(413, "too large")
        directory = os.path.join(cfg.artifacts_dir, str(job["song_id"]))
        os.makedirs(directory, exist_ok=True)
        temporary = os.path.join(directory, ".vocals.ogg.tmp")
        size = 0
        with open(temporary, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    out.close()
                    os.unlink(temporary)
                    raise HTTPException(413, "too large")
                out.write(chunk)
        os.replace(temporary, os.path.join(directory, "vocals.ogg"))
        return {"ok": True}

    @app.post("/api/worker/jobs/{job_id}/result")
    def job_result(job_id: int, body: dict, name: str = Depends(worker_auth)):
        job = _running(job_id, name)
        lines = body.get("lines")
        if not isinstance(lines, list) or not lines:
            raise HTTPException(422, "lines expected")
        outcome = operations.save_ai_result(db, cfg, job["song_id"], lines, str(body.get("method", ""))[:200])
        db.run("UPDATE jobs SET status='done', stage=?, finished_at=? WHERE id=?", (outcome[:80], int(time.time()), job_id))
        return {"ok": True, "result": outcome}

    @app.post("/api/worker/jobs/{job_id}/fail")
    def job_fail(job_id: int, body: dict, name: str = Depends(worker_auth)):
        job = _running(job_id, name)
        error = str(body.get("error", "failed"))[:500]
        db.run("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?", (error, int(time.time()), job_id))
        db.event("ai", f"AI sync failed: {error}", job["song_id"])
        return {"ok": True}

    # --- settings -----------------------------------------------------------------------

    @app.get("/api/settings")
    def settings(me: dict = Depends(user)):
        return {"library": cfg.library, "read_only": cfg.read_only, "retry_days": cfg.retry_days,
                "scan_interval": cfg.scan_interval, "lrclib_allowed": cfg.lrclib,
                "lrclib": cfg.lrclib and db.setting("lrclib", "1") == "1", "version": __version__}

    @app.put("/api/settings")
    def update_settings(body: dict, me: dict = Depends(user)):
        if "lrclib" in body:
            db.set_setting("lrclib", "1" if body["lrclib"] else "0")
        return settings(me)

    @app.post("/api/library/scan")
    def rescan(me: dict = Depends(user)):
        worker.wake.set()
        return {"ok": True}

    @app.get("/api/events")
    def events(limit: int = 100, me: dict = Depends(user)):
        return db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (max(1, min(500, limit)),))

    # --- the interface ------------------------------------------------------------------

    static = os.path.realpath(cfg.static_dir)

    @app.get("/{path:path}", include_in_schema=False)
    def interface(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "no such endpoint")
        full = os.path.realpath(os.path.join(static, path))
        if path and full.startswith(static + os.sep) and os.path.isfile(full):
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in full else {}
            return FileResponse(full, headers=headers)
        index = os.path.join(static, "index.html")
        if not os.path.exists(index):
            return JSONResponse({"detail": "interface not built"}, 503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
