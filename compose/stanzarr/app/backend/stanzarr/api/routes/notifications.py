from fastapi import APIRouter, Response

from ... import events
from ...notifications import NotificationIn
from .. import schemas
from ..auth import need
from ..deps import Ctx

router = APIRouter(prefix="/notifications", tags=["Notifications"], dependencies=[need("settings")])
_PROBLEMS = {403: {"model": schemas.Problem}, 404: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


@router.get("/types", summary="The notification types, their fields, and the events to choose from",
            response_model=schemas.NotificationTypes)
def notification_types(ctx: Ctx):
    return {"types": [kind.describe() for kind in ctx.notifications.types.values()],
            "events": [vars(item) for item in events.CATALOG]}


@router.get("", summary="Where Stanzarr's events go", response_model=schemas.NotificationList)
def list_notifications(ctx: Ctx):
    return {"items": ctx.notifications.list()}


@router.post("", status_code=201, summary="Add a notification", response_model=schemas.Notification,
             responses=_PROBLEMS)
def create_notification(body: NotificationIn, ctx: Ctx):
    return ctx.notifications.create(body.model_dump())


@router.post("/test", summary="Send a test message with a notification not saved yet",
             response_model=schemas.TestResult, responses=_PROBLEMS)
async def test_new_notification(body: NotificationIn, ctx: Ctx):
    return await ctx.notifier.test(body.model_dump())


@router.get("/{item_id}", summary="One notification", response_model=schemas.Notification, responses=_PROBLEMS)
def get_notification(item_id: str, ctx: Ctx):
    return ctx.notifications.get(item_id)


@router.put("/{item_id}", summary="Change a notification; a secret left empty keeps its value",
            response_model=schemas.Notification, responses=_PROBLEMS)
def update_notification(item_id: str, body: NotificationIn, ctx: Ctx):
    return ctx.notifications.update(item_id, body.model_dump())


@router.post("/{item_id}/test", summary="Send a test message with a saved notification, changed as the body says",
             response_model=schemas.TestResult, responses=_PROBLEMS)
async def test_notification(item_id: str, body: NotificationIn, ctx: Ctx):
    return await ctx.notifier.test(body.model_dump(), item_id)


@router.delete("/{item_id}", status_code=204, summary="Remove a notification", responses=_PROBLEMS)
def delete_notification(item_id: str, ctx: Ctx):
    ctx.notifications.delete(item_id)
    return Response(status_code=204)
