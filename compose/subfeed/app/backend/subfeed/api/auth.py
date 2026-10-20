"""Who asks, for every route but the public ones (health, signing in): a
person by the page's session cookie, or an app by its API key
(batlab_common/web.py). What they may do is checked by need()."""

from batlab_common.web import Gate, RequestLog, without_key  # noqa: F401 (the app's and routes' too)

from ..accounts import KEY_DEFAULT, PERMISSIONS

gate = Gate("subfeed", PERMISSIONS, user_default=["feeds"], key_default=KEY_DEFAULT, key_for="clipseerr, Homepage")
principal = gate.principal
Who = gate.Who
need = gate.need
