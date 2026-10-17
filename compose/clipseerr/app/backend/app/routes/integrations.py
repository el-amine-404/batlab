"""Services (Settings, Services) and notifications (Settings, Notifications):
the same routes for both, under /services and /notifications."""

from fastapi import APIRouter, Response

from .. import events, outbound, schemas
from ..context import engine, http, notification_store, notifier, service_store
from ..integrations import IntegrationIn
from ..services import Context

_PROBLEMS = {404: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


async def _test_service(raw: dict, item_id: str | None = None) -> dict:
    _body, kind, config = service_store.parse(raw, item_id)
    try:
        message = await kind.test(Context(http=http, callback_base=""), config)
    except outbound.SendFailed as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": message}


def _routes(prefix: str, tag: str, store, test, event_list) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    noun = tag.lower().rstrip("s")

    @router.get("/types", summary=f"The {noun} types and their fields", response_model=schemas.IntegrationTypes)
    def types():
        return {"types": [kind.describe() for kind in store.types.values()], "events": event_list}

    @router.get("", summary=f"Every {noun}, in order", response_model=schemas.IntegrationList)
    def list_items():
        return {"items": store.list()}

    @router.post("", status_code=201, summary=f"Add a {noun}", response_model=schemas.Integration,
                 responses=_PROBLEMS)
    def create(body: IntegrationIn):
        item = store.create(body.model_dump())
        engine.wake()
        return item

    @router.post("/test", summary=f"Try a {noun} not saved yet", response_model=schemas.TestResult,
                 responses=_PROBLEMS)
    async def test_new(body: IntegrationIn):
        return await test(body.model_dump())

    @router.put("/order", summary=f"Put the {noun}s in order", response_model=schemas.IntegrationList,
                responses=_PROBLEMS)
    def order(body: schemas.Order):
        return {"items": store.reorder(body.ids)}

    @router.get("/{item_id}", summary=f"One {noun}", response_model=schemas.Integration, responses=_PROBLEMS)
    def get(item_id: str):
        return store.get(item_id)

    @router.put("/{item_id}", summary=f"Change a {noun}; a secret left empty keeps its value",
                response_model=schemas.Integration, responses=_PROBLEMS)
    def update(item_id: str, body: IntegrationIn):
        item = store.update(item_id, body.model_dump())
        engine.wake()
        return item

    @router.post("/{item_id}/test", summary=f"Try a saved {noun}, changed as the body says",
                 response_model=schemas.TestResult, responses=_PROBLEMS)
    async def test_saved(item_id: str, body: IntegrationIn):
        return await test(body.model_dump(), item_id)

    @router.delete("/{item_id}", status_code=204, summary=f"Remove a {noun}", responses=_PROBLEMS)
    def delete(item_id: str):
        store.delete(item_id)
        return Response(status_code=204)

    return router


services_router = _routes("/services", "Services", service_store, _test_service, [])
notifications_router = _routes("/notifications", "Notifications", notification_store, notifier.test,
                               [vars(item) for item in events.CATALOG])
