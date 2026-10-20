"""Who asks, for every route but the public ones (health, signing in): a
person by the page's session cookie, or an app by its API key
(batlab_common/web.py). What they may do is checked by need()."""

from batlab_common.web import Gate, RequestLog  # noqa: F401 (main.py's too)

from .accounts import KEY_DEFAULT, PERMISSIONS

gate = Gate("songdrop", PERMISSIONS, user_default=["download"], key_default=KEY_DEFAULT, key_for="clipseerr, a script")
principal = gate.principal
Who = gate.Who
need = gate.need
