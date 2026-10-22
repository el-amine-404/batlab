"""The lyrics sources (Settings, Sources): add-ons asked first to last."""

import asyncio

from fastapi import APIRouter, Response

from ...sources import SourceIn
from .. import schemas
from ..auth import need
from ..deps import Ctx

router = APIRouter(prefix="/sources", tags=["Sources"], dependencies=[need("settings")])
_PROBLEMS = {403: {"model": schemas.Problem}, 404: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


async def _test(ctx, raw: dict, item_id: str | None = None) -> dict:
    _body, kind, config = ctx.sources.parse(raw, item_id)
    try:
        return {"ok": True, "message": await asyncio.to_thread(kind.test, config)}
    except Exception as exc:
        return {"ok": False, "message": f"{kind.label} didn't answer: {exc}"}


@router.get("/types", summary="The kinds of source there are, and their fields", response_model=schemas.SourceTypes)
def source_types(ctx: Ctx):
    return {"types": [kind.describe() for kind in ctx.sources.types.values()]}


@router.get("", summary="The sources, first asked first", response_model=schemas.SourceList)
def list_sources(ctx: Ctx):
    return {"items": ctx.sources.list()}


@router.post("", status_code=201, summary="Add a source", response_model=schemas.Source, responses=_PROBLEMS)
def create_source(body: SourceIn, ctx: Ctx):
    return ctx.sources.create(body.model_dump())


@router.post("/test", summary="Ask a source not saved yet for a song it should know",
             response_model=schemas.TestResult, responses=_PROBLEMS)
async def test_new_source(body: SourceIn, ctx: Ctx):
    return await _test(ctx, body.model_dump())


@router.put("/order", summary="Put the sources in order", response_model=schemas.SourceList, responses=_PROBLEMS)
def order_sources(body: schemas.Order, ctx: Ctx):
    return {"items": ctx.sources.reorder(body.ids)}


@router.get("/{item_id}", summary="One source", response_model=schemas.Source, responses=_PROBLEMS)
def get_source(item_id: str, ctx: Ctx):
    return ctx.sources.get(item_id)


@router.put("/{item_id}", summary="Change a source; a secret left empty keeps its value",
            response_model=schemas.Source, responses=_PROBLEMS)
def update_source(item_id: str, body: SourceIn, ctx: Ctx):
    return ctx.sources.update(item_id, body.model_dump())


@router.post("/{item_id}/test", summary="Ask a saved source, changed as the body says, for a song it should know",
             response_model=schemas.TestResult, responses=_PROBLEMS)
async def test_source(item_id: str, body: SourceIn, ctx: Ctx):
    return await _test(ctx, body.model_dump(), item_id)


@router.delete("/{item_id}", status_code=204, summary="Remove a source", responses=_PROBLEMS)
def delete_source(item_id: str, ctx: Ctx):
    ctx.sources.delete(item_id)
    return Response(status_code=204)
