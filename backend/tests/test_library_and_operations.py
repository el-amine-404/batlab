import os
from unittest import mock

import pytest

from stanzarr import library, operations
from stanzarr.db import Database

from .conftest import requires_ffmpeg, tone


@pytest.fixture
def db(cfg):
    cfg.ensure_dirs()
    return Database(cfg.db_path)


def status(db, song_id):
    return db.one(f"SELECT {library.STATUS_SQL} AS s FROM songs s WHERE s.id=?", (song_id,))["s"]


@requires_ffmpeg
def test_scan_finds_songs_and_sidecars(db, library):
    song = tone(os.path.join(library, "Band", "Record (2020)", "01 - Song.mp3"))
    with open(song[:-4] + ".txt", "w") as out:
        out.write("one line\nanother line\n")
    counts = library_scan(db, library)
    assert counts["added"] == 1
    row = db.one("SELECT * FROM songs")
    assert (row["artist"], row["title"]) == ("Band", "Song")
    assert status(db, row["id"]) == "plain"
    with open(song[:-4] + ".lrc", "w") as out:
        out.write("[00:01.00] one line\n")
    library_scan(db, library)
    assert status(db, row["id"]) == "synced"
    os.unlink(song)
    assert library_scan(db, library)["removed"] == 1


def library_scan(db, root):
    return library.scan(db, [root])


@requires_ffmpeg
def test_lrclib_synced_replaces_ai_but_never_a_persons(db, cfg, library):
    song = tone(os.path.join(library, "Band", "Record", "01 - Song.mp3"))
    library_scan(db, library)
    song_id = db.one("SELECT id FROM songs")["id"]
    lrc = song[:-4] + ".lrc"
    with open(lrc, "w") as out:
        out.write("[by:AI-generated, corrected by a person]\n[00:02.00] ai words\n")
    with mock.patch("stanzarr.lrclib.lookup", return_value=("synced", "[00:01.00] real words\n", 42)):
        result = operations.search_lrclib(db, cfg, song_id)
    assert "replaced" in result
    assert "real words" in open(lrc).read()
    assert os.listdir(cfg.backups_dir), "the AI file is kept"
    assert status(db, song_id) == "synced"
    with mock.patch("stanzarr.lrclib.lookup", return_value=("synced", "[00:01.00] other words\n", 43)):
        operations.search_lrclib(db, cfg, song_id)
    assert "real words" in open(lrc).read(), "a person-synced file is never overwritten"


@requires_ffmpeg
def test_editor_save_keeps_the_ai_flag(db, cfg, library):
    song = tone(os.path.join(library, "Band", "Record", "01 - Song.mp3"))
    library_scan(db, library)
    song_id = db.one("SELECT id FROM songs")["id"]
    lrc = song[:-4] + ".lrc"
    with open(lrc, "w") as out:
        out.write("[by:AI-generated, not checked by a person]\n[#:uncertain lines: 2]\n[00:01.00] a\n[00:03.00] b\n")
    data = operations.editor_lines(db, song_id)
    assert [l["u"] for l in data["lines"]] == [False, True]
    operations.save_from_editor(db, cfg, song_id, [{"t": 1.2, "s": "a", "fixed": True}, {"t": 3, "s": "b", "u": True}])
    text = open(lrc).read()
    assert text.startswith("[by:AI-generated, corrected by a person]")
    assert "[#:uncertain lines (the AI was unsure): 2]" in text and "[#:checked lines: 1]" in text
    assert status(db, song_id) == "ai_corrected"


@requires_ffmpeg
def test_plain_lyrics_synced_by_hand_are_a_persons(db, cfg, library):
    song = tone(os.path.join(library, "Band", "Record", "01 - Song.mp3"))
    with open(song[:-4] + ".txt", "w") as out:
        out.write("one\ntwo\n")
    library_scan(db, library)
    song_id = db.one("SELECT id FROM songs")["id"]
    data = operations.editor_lines(db, song_id)
    assert data["origin"] == "plain" and len(data["lines"]) == 2
    operations.save_from_editor(db, cfg, song_id, data["lines"])
    assert open(song[:-4] + ".lrc").read().startswith("[by:a person]")
    assert os.path.exists(song[:-4] + ".txt"), "plain lyrics are kept"
    assert status(db, song_id) == "synced"


@requires_ffmpeg
def test_ai_result_never_over_a_persons_lrc(db, cfg, library):
    song = tone(os.path.join(library, "Band", "Record", "01 - Song.mp3"))
    library_scan(db, library)
    song_id = db.one("SELECT id FROM songs")["id"]
    with open(song[:-4] + ".lrc", "w") as out:
        out.write("[00:01.00] by a person\n")
    assert "kept" in operations.save_ai_result(db, cfg, song_id, [{"t": 1, "s": "x"}], "test")
    assert "by a person" in open(song[:-4] + ".lrc").read()


@requires_ffmpeg
def test_read_only_writes_nothing(db, cfg, library):
    cfg.read_only = True
    song = tone(os.path.join(library, "Band", "Record", "01 - Song.mp3"))
    library_scan(db, library)
    song_id = db.one("SELECT id FROM songs")["id"]
    with mock.patch("stanzarr.lrclib.lookup", return_value=("synced", "[00:01.00] words\n", 1)):
        assert "read-only" in operations.search_lrclib(db, cfg, song_id)
    assert not os.path.exists(song[:-4] + ".lrc")
    with pytest.raises(operations.ReadOnly):
        operations.save_from_editor(db, cfg, song_id, [{"t": 1, "s": "a"}])
