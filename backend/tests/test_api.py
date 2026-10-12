import os

from stanzarr import library as lib

from .conftest import requires_ffmpeg, tone


def test_setup_once_then_login(client):
    assert client.get("/api/session").json()["setup"] is True
    assert client.get("/api/stats").status_code == 401
    assert client.post("/api/setup", json={"username": "admin", "password": "short"}).status_code == 422
    assert client.post("/api/setup", json={"username": "admin", "password": "correct horse"}).status_code == 200
    assert client.post("/api/setup", json={"username": "x", "password": "another one"}).status_code == 409
    client.post("/api/logout")
    assert client.get("/api/stats").status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "correct horse"}).status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_login_attempts_are_limited(client):
    client.post("/api/setup", json={"username": "admin", "password": "correct horse"})
    client.post("/api/logout")
    codes = [client.post("/api/login", json={"username": "admin", "password": "x"}).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_cross_site_and_form_posts_are_refused(signed_in):
    assert signed_in.post("/api/library/scan", headers={"Origin": "http://evil.example"}).status_code == 403
    r = signed_in.put("/api/settings", content="lrclib=0", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


@requires_ffmpeg
def test_library_listing_and_filters(signed_in, cfg, library):
    one = tone(os.path.join(library, "A", "R", "01 - One.mp3"), title="One", artist="A")
    tone(os.path.join(library, "B", "R", "01 - Two.mp3"), title="Two", artist="B")
    with open(one[:-4] + ".lrc", "w") as out:
        out.write("[00:01.00] I know you love me and all the things that you do\n")
    lib.scan(signed_in.app_state.db, cfg.library)
    everything = signed_in.get("/api/songs").json()
    assert everything["total"] == 2
    synced = signed_in.get("/api/songs", params={"status": "synced"}).json()["songs"]
    assert [s["title"] for s in synced] == ["One"] and synced[0]["formats"] == ["lrc"]
    assert synced[0]["languages"][0]["code"] == "en"
    stats = signed_in.get("/api/stats").json()
    assert stats["by_status"]["synced"] == 1 and stats["by_status"]["missing"] == 1


@requires_ffmpeg
def test_ai_sync_needs_a_gpu_worker_then_runs_end_to_end(signed_in, cfg, library):
    song = tone(os.path.join(library, "A", "R", "01 - One.mp3"), title="One", artist="A")
    with open(song[:-4] + ".txt", "w") as out:
        out.write("first line\nsecond line\n")
    lib.scan(signed_in.app_state.db, cfg.library)
    song_id = signed_in.get("/api/songs").json()["songs"][0]["id"]
    assert signed_in.post(f"/api/songs/{song_id}/ai-sync").status_code == 409  # no worker
    key = signed_in.get("/api/workers").json()["key"]
    worker = {"X-Stanzarr-Key": key, "X-Stanzarr-Worker": "laptop"}
    assert signed_in.post("/api/worker/heartbeat", json={"gpu": ""}, headers={"X-Stanzarr-Key": "wrong"}).status_code == 401
    signed_in.post("/api/worker/heartbeat", json={"gpu": ""}, headers=worker)
    assert signed_in.post(f"/api/songs/{song_id}/ai-sync").status_code == 409  # a worker, but no GPU
    signed_in.post("/api/worker/heartbeat", json={"gpu": "Quadro T1000", "vram": 4096}, headers=worker)
    assert signed_in.get("/api/status").json()["gpu_online"] is True
    assert signed_in.post(f"/api/songs/{song_id}/ai-sync").status_code == 200
    job = signed_in.post("/api/worker/next", headers=worker).json()
    assert job["lines"] == ["first line", "second line"]
    audio = signed_in.get(f"/api/worker/jobs/{job['job']}/audio", headers=worker)
    assert audio.status_code == 200 and len(audio.content) > 1000
    assert signed_in.put(f"/api/worker/jobs/{job['job']}/voice", content=b"OggS fake", headers=worker).status_code == 200
    r = signed_in.post(f"/api/worker/jobs/{job['job']}/result", headers=worker,
                       json={"lines": [{"t": 1.0, "s": "first line"}, {"t": 4.5, "s": "second line", "u": True}],
                             "method": "test"})
    assert r.status_code == 200
    text = open(song[:-4] + ".lrc").read()
    assert text.startswith("[by:AI-generated, not checked by a person]") and "uncertain lines (the methods disagree): 2" in text
    assert signed_in.get(f"/api/songs/{song_id}").json()["song"]["status"] == "ai_unchecked"
    assert signed_in.post("/api/worker/next", headers=worker).status_code == 204


@requires_ffmpeg
def test_editor_roundtrip_and_audio_ranges(signed_in, cfg, library):
    song = tone(os.path.join(library, "A", "R", "01 - One.mp3"), title="One", artist="A")
    with open(song[:-4] + ".txt", "w") as out:
        out.write("first\nsecond\n")
    lib.scan(signed_in.app_state.db, cfg.library)
    song_id = signed_in.get("/api/songs").json()["songs"][0]["id"]
    data = signed_in.get(f"/api/songs/{song_id}/editor").json()
    assert data["origin"] == "plain" and len(data["lines"]) == 2
    assert signed_in.put(f"/api/songs/{song_id}/lyrics", json={"lines": data["lines"]}).json()["saved"] == 2
    ranged = signed_in.get(f"/api/songs/{song_id}/audio/mix", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    peaks = signed_in.get(f"/api/songs/{song_id}/peaks/mix").json()
    assert peaks["rate"] == 200 and abs(len(peaks["peaks"]) / 200 - 30) < 0.2
