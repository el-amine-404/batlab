"""musicguard: every music file is checked, stripped to audio and clean tags,
and filed, or it is quarantined.

  inbox   files from pasted links (MeTube, spotDL) are checked, rebuilt and
          filed into the extras library, then removed from the inbox
  roots   new or changed files in the libraries are checked where they are:
          rebuilt in Aurral's and the extras library, verified only in
          Lidarr's, which owns its tags (see verify())
  migrate a one-time copy of an old library into the layout, source untouched

What "checked and rebuilt" means is in docs/music.md; in short: no program,
archive or script gets through, clamd sees every file it can, the audio must
decode end to end, only the audio stream is copied into a new container, only
allow-listed tags are written back after checking each value, cover art is
decoded and encoded again, and lossless files made from lossy ones are
reported.
"""

import argparse
import os
import signal
import sys
import time

import credits as credit_fix

from settings import AUDIO_EXTENSIONS, Config
from parts.state import StateMixin
from parts.reporting import ReportingMixin
from parts.announce import AnnounceMixin
from parts.quarantine import QuarantineMixin
from parts.checks import ChecksMixin
from parts.filing import FilingMixin
from parts.lyrics_files import LyricsMixin
from parts.passes import PassesMixin
from parts.upgrades import UpgradesMixin
from parts.duplicates import DuplicatesMixin


class Guard(StateMixin, ReportingMixin, AnnounceMixin, QuarantineMixin, ChecksMixin, FilingMixin, LyricsMixin, PassesMixin, UpgradesMixin, DuplicatesMixin):
    """The guard: every part in parts/, sharing the config, the state and
    the events."""

    def __init__(self, config):
        self.config = config
        self.state_path = os.path.join(config.state_dir, "state.json")
        self.state = self.load_state()
        self.events = []
        self.clamd_ok_at = 0.0
        self.seen_sizes = {}
        self.held_reported = set()
        self.last_inbox = 0.0
        self.credits = credit_fix.Credits(self.state["releases"])
        # path -> (size, mtime, title, artist, seconds), for finding duplicates.
        self.songs = {}
        self.tracks = {}  # path -> (size, mtime_ns, dedup.Track), for dedup_pass
        # Navidrome is told to scan once a library pass leaves nothing unchecked.
        self.library_changed = True
        self.scan_reported = False
        # Library files that became playable since the last scan, announced after it.
        self.ready = []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="watch the inbox and libraries until stopped")
    commands.add_parser("once", help="one inbox pass and one library pass")
    migrate = commands.add_parser("migrate", help="copy an old library into the layout")
    migrate.add_argument("source")
    migrate.add_argument("destination")
    check = commands.add_parser("check", help="check one file and say what would happen")
    check.add_argument("path")
    audit = commands.add_parser("audit", help="compare every track's tags with what Shazam hears")
    audit.add_argument("roots", nargs="*")
    commands.add_parser("upgrade", help="one pass handing songs from links to Lidarr")
    commands.add_parser("dedup", help="one pass retiring worse copies of songs filed twice")
    commands.add_parser("lyrics", help="ask LRCLIB now for every track without synced lyrics")
    ident = commands.add_parser("identify", help="ask Shazam what files are")
    ident.add_argument("paths", nargs="+")
    arguments = parser.parse_args(argv)

    config = Config()
    if arguments.command == "check":
        config.dry_run = True
        config.lyrics = False
    guard = Guard(config)

    if arguments.command == "check":
        guard.process(os.path.abspath(arguments.path), "copy", os.path.dirname(arguments.path),
                      "/would-be-library")
        return 0
    if arguments.command == "identify":
        for path in arguments.paths:
            print(f"{path}: {guard.shazam(path)!r}", flush=True)
        return 0
    if arguments.command == "lyrics":
        for root in config.roots:
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in sorted(filenames):
                    path = os.path.join(directory, name)
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    guard.state["lyrics"].pop(path, None)  # ignore the 30-day wait
                    guard.lyrics_for_existing(path)
        return 0
    if arguments.command == "dedup":
        guard.dedup_pass()
        guard.save_state()
        return 0
    if arguments.command == "upgrade":
        guard.upgrade_pass()
        guard.save_state()
        return 0
    if arguments.command == "audit":
        guard.audit(arguments.roots or config.roots)
        return 0
    if arguments.command == "migrate":
        guard.migrate(arguments.source, arguments.destination)
        guard.notify()
        return 0

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    last_roots = 0.0
    while not stopping:
        guard.heartbeat()
        guard.inbox_pass()
        if time.monotonic() - last_roots >= config.roots_interval or arguments.command == "once":
            guard.roots_pass()
            guard.dedup_pass()
            guard.upgrade_pass()
            guard.purge_quarantine()
            guard.scan_navidrome()
            last_roots = time.monotonic()
        guard.save_state()
        guard.notify()
        if arguments.command == "once":
            break
        for _ in range(config.inbox_interval):
            if stopping:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
