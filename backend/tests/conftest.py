import os
import shutil
import subprocess

import mutagen
import pytest
from fastapi.testclient import TestClient

from stanzarr.app import create_app
from stanzarr.config import Config


def tone(path: str, seconds: int = 30, title: str = "Song", artist: str = "Band", album: str = "Record") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-ac", "2", path], check=True)
    audio = mutagen.File(path, easy=True)
    if audio.tags is None:
        audio.add_tags()
    audio["title"], audio["artist"], audio["album"] = title, artist, album
    audio.save()
    return path


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    return str(root)


@pytest.fixture
def cfg(tmp_path, library):
    c = Config()
    c.library = [library]
    c.config_dir = str(tmp_path / "config")
    c.static_dir = str(tmp_path / "static")
    c.lrclib = False
    c.username = c.password = c.worker_key = ""
    c.read_only = False
    return c


@pytest.fixture
def client(cfg):
    app = create_app(cfg, background=False)
    with TestClient(app, base_url="http://stanzarr.test") as c:
        c.app_state = app.state
        yield c


@pytest.fixture
def signed_in(client):
    r = client.post("/api/setup", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 200
    return client


requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
