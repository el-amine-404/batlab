"""Who asks, for every route but the public ones (health, signing in, the
apps' callbacks): a person by the page's session cookie, or an app by its API
key (batlab_common/web.py). What they may do is checked by need()."""

from batlab_common.web import Gate, RequestLog  # noqa: F401 (main.py's too)

from .accounts import KEY_DEFAULT, PERMISSIONS

gate = Gate("clipseerr", PERMISSIONS, user_default=["request"], key_default=KEY_DEFAULT, key_for="a script",
            package="app")
principal = gate.principal
Who = gate.Who
need = gate.need
