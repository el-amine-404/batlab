import logging

from fastapi import APIRouter, Request, Response

from .. import cookies, preferences, problems, schemas
from ..context import base_settings, manager

log = logging.getLogger("songdrop")
router = APIRouter(tags=["Settings"])


@router.get("/settings", summary="The current settings", response_model=schemas.Settings)
def get_settings():
    return preferences.public(manager.settings)


@router.patch("/settings", summary="Change some settings; the others keep their value",
              response_model=schemas.Settings, responses={422: {"model": schemas.Problem}})
def patch_settings(body: preferences.SettingsPatch):
    """Applies to the next job and survives a restart. 422 with the reason when
    a value isn't valid; nothing is changed then."""
    changes = body.model_dump(exclude_unset=True)
    manager.settings = preferences.update(base_settings, base_settings.state_dir, changes)
    log.info("settings changed: %s", ", ".join(sorted(changes)))
    return preferences.public(manager.settings)


@router.get("/cookies", summary="Whether YouTube cookies are saved, and until when",
            response_model=schemas.CookieStatus, response_model_exclude_none=True)
def cookie_status():
    return cookies.status(base_settings.state_dir)


@router.put("/cookies", summary="Save a cookies.txt (the request body is the file)",
            response_model=schemas.CookieStatus, response_model_exclude_none=True,
            responses={400: {"model": schemas.Problem}, 413: {"model": schemas.Problem}})
async def put_cookies(request: Request):
    """The body is the cookies.txt itself (Netscape format, at most 1 MB, with
    youtube.com cookies): `curl -X PUT --data-binary @cookies.txt …`."""
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > cookies.MAX_BYTES:
            raise problems.Problem(413, "That file is too big for a cookies.txt.")
    return cookies.save(base_settings.state_dir, data)


@router.delete("/cookies", status_code=204, summary="Remove the saved cookies")
def delete_cookies():
    cookies.delete(base_settings.state_dir)
    return Response(status_code=204)
