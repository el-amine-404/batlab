"""Who asks, for every route but the public ones (health, signing in): a
person by the page's session cookie, or an app by its API key
(batlab_common/web.py). What they may do is checked by need()."""

from batlab_common.web import Gate, address  # noqa: F401 (address: the routes' too)

from ..accounts import KEY_DEFAULT, PERMISSIONS

gate = Gate("videodrop", PERMISSIONS, user_default=["download"], key_default=KEY_DEFAULT)
principal = gate.principal
Who = gate.Who
need = gate.need
PAGE_HEADER = gate.page_header
