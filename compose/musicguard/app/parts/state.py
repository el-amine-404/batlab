"""The state file (/state/state.json) and the heartbeat.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import json
import os
import tempfile


from settings import STATE_VERSION
from common import now_iso


class StateMixin:
    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if state.get("version") == STATE_VERSION:
                state.setdefault("audited", {})
                state.setdefault("releases", {})
                state.setdefault("upgrades", {})
                state.setdefault("singles", {})
                state.setdefault("sidecars", {})
                state.setdefault("flagged", {})
                return state
        except (OSError, ValueError):
            pass
        return {"version": STATE_VERSION, "files": {}, "lyrics": {}, "audited": {}, "releases": {},
                "upgrades": {}, "singles": {}, "sidecars": {}, "flagged": {}}

    def save_state(self):
        if self.config.dry_run:
            return
        os.makedirs(self.config.state_dir, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.config.state_dir, prefix=".state.")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(self.state, out)
        os.replace(temporary, self.state_path)

    def remember(self, path):
        stat = os.stat(path)
        self.state["files"][path] = [stat.st_size, stat.st_mtime_ns]

    def heartbeat(self):
        os.makedirs(self.config.state_dir, exist_ok=True)
        with open(os.path.join(self.config.state_dir, "heartbeat"), "w", encoding="utf-8") as out:
            out.write(now_iso())
