"""How a runner reports back: one JSON object per line on the file descriptor
named by SONGDROP_EVENTS_FD, so whatever the engine prints on stdout or stderr
never mixes with the events. Standard library only: the spotDL runner imports
this from spotDL's own virtualenv.

Events, all with a "type":
  meta        title, total            what the link holds
  item_start  index, name             a song started
  progress    index, percent          download progress of a song (0-100)
  file        index, path, title, artist
  item_error  index, name, message, url?   one song failed; the job goes on
                                      (url: the song's own link, to retry it alone)
  note        index, name, message    worth knowing: a wrong upload replaced
  fatal       message                 the whole job failed
"""

import json
import os
import sys
import threading
from typing import NoReturn


class Events:
    def __init__(self, fd: int):
        self._stream = os.fdopen(fd, "w", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "Events":
        return cls(int(os.environ["SONGDROP_EVENTS_FD"]))

    def emit(self, type_: str, **fields) -> None:
        line = json.dumps({"type": type_, **fields}, ensure_ascii=False, default=str)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def fatal(self, message: str) -> NoReturn:
        self.emit("fatal", message=message)
        sys.exit(1)


def read_config() -> dict:
    return json.load(sys.stdin)
