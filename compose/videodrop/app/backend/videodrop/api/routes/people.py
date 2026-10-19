import logging

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool

from .. import schemas
from ..auth import need
from ..deps import Ctx

log = logging.getLogger("videodrop.auth")

# All of it the admins'.
router = APIRouter(tags=["People and keys"], dependencies=[need("admin")])
_PROBLEMS = {403: {"model": schemas.Problem}, 404: {"model": schemas.Problem}, 409: {"model": schemas.Problem},
             422: {"model": schemas.Problem}}


@router.get("/users", summary="The people who can sign in", response_model=schemas.UserList)
def list_users(ctx: Ctx):
    return {"users": ctx.accounts.users()}


@router.post("/users", status_code=201, summary="Add someone", response_model=schemas.User, responses=_PROBLEMS)
async def add_user(body: schemas.UserIn, ctx: Ctx, who=need("admin")):
    user = await run_in_threadpool(ctx.accounts.create_user, body.username, body.name, body.password,
                                   body.permissions)
    log.info("%s added %s (%s), who may: %s", who.name, user["username"], user["name"],
             ", ".join(user["permissions"]) or "nothing")
    return user


@router.get("/users/{user_id}", summary="One person", response_model=schemas.User, responses=_PROBLEMS)
def get_user(user_id: str, ctx: Ctx):
    return ctx.accounts.user(user_id)


@router.patch("/users/{user_id}", summary="Change someone's name, permissions, or turn them off",
              response_model=schemas.User, responses=_PROBLEMS)
def change_user(user_id: str, body: schemas.UserPatch, ctx: Ctx, who=need("admin")):
    user = ctx.accounts.update_user(user_id, name=body.name, permissions=body.permissions, enabled=body.enabled,
                                    acting=who)
    log.info("%s changed %s: %s", who.name, user["username"], body.model_dump(exclude_none=True))
    return user


@router.put("/users/{user_id}/password", status_code=204, summary="Give someone a new password; their sessions end",
            responses=_PROBLEMS)
async def reset_password(user_id: str, body: schemas.NewPassword, ctx: Ctx, who=need("admin")):
    await run_in_threadpool(ctx.accounts.set_password, user_id, body.password,
                            keep_session=who.session if who.id == user_id else None)
    log.info("%s gave %s a new password", who.name, ctx.accounts.user(user_id)["username"])


@router.delete("/users/{user_id}", status_code=204, summary="Remove someone; their downloads stay",
               responses=_PROBLEMS)
def remove_user(user_id: str, ctx: Ctx, who=need("admin")):
    username = ctx.accounts.user(user_id)["username"]
    ctx.accounts.delete_user(user_id, acting=who)
    log.info("%s removed %s", who.name, username)
    return Response(status_code=204)


@router.get("/keys", summary="The apps' API keys", response_model=schemas.ApiKeyList)
def list_keys(ctx: Ctx):
    return {"keys": ctx.accounts.keys()}


@router.post("/keys", status_code=201, summary="Make an API key; it is shown this once",
             response_model=schemas.ApiKeyMade, responses=_PROBLEMS)
def add_key(body: schemas.ApiKeyIn, ctx: Ctx, who=need("admin")):
    token, key = ctx.accounts.create_key(body.name, body.permissions, who.id)
    log.info("%s made the API key %s (%s…), which may: %s", who.name, key["name"], key["prefix"],
             ", ".join(key["permissions"]) or "read only")
    return {**key, "key": token}


@router.patch("/keys/{key_id}", summary="Change what a key may do", response_model=schemas.ApiKey,
              responses=_PROBLEMS)
def change_key(key_id: str, body: schemas.ApiKeyPatch, ctx: Ctx, who=need("admin")):
    key = ctx.accounts.update_key(key_id, body.permissions)
    log.info("%s changed the API key %s: %s", who.name, key["name"], ", ".join(key["permissions"]) or "read only")
    return key


@router.delete("/keys/{key_id}", status_code=204, summary="Remove an API key: the app using it is refused from now on",
               responses=_PROBLEMS)
def remove_key(key_id: str, ctx: Ctx, who=need("admin")):
    name = next((k["name"] for k in ctx.accounts.keys() if k["id"] == key_id), key_id)
    ctx.accounts.delete_key(key_id)
    log.info("%s removed the API key %s", who.name, name)
    return Response(status_code=204)
