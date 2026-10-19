from fastapi import APIRouter, Query, Response

from ... import folders
from ...destinations import DestinationIn, DestinationPatch
from ...options import DEFAULT_TEMPLATE, FIELDS
from .. import schemas
from ..auth import need
from ..deps import Ctx

router = APIRouter(tags=["Destinations"])
_PROBLEMS = {404: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


@router.get("/destinations", summary="Where downloads can go", response_model=schemas.DestinationList)
def list_destinations(ctx: Ctx):
    return {"destinations": [ctx.destinations.public(row) for row in ctx.destinations.rows()]}


@router.post("/destinations", status_code=201, summary="Add a destination", response_model=schemas.Destination, responses=_PROBLEMS,
             dependencies=[need("settings")])
def create_destination(body: DestinationIn, ctx: Ctx):
    """Its folder is checked (inside the media folder, writable) and made
    when missing, with the first video."""
    return ctx.destinations.public(ctx.destinations.create(body))


@router.get("/destinations/template", summary="The fields a destination's file name can hold",
            response_model=schemas.TemplateFields)
def template_fields():
    return {"fields": list(FIELDS), "default": DEFAULT_TEMPLATE}


@router.get("/destinations/{slug}", summary="One destination", response_model=schemas.Destination,
            responses=_PROBLEMS)
def get_destination(slug: str, ctx: Ctx):
    return ctx.destinations.public(ctx.destinations.row(slug))


@router.patch("/destinations/{slug}", summary="Change a destination; what is left out keeps its value",
              response_model=schemas.Destination, responses=_PROBLEMS,
              dependencies=[need("settings")])
def update_destination(slug: str, body: DestinationPatch, ctx: Ctx):
    """Downloads asked for from now on follow the change; files already
    there, and downloads already asked for, stay as they are."""
    return ctx.destinations.public(ctx.destinations.update(slug, body))


@router.delete("/destinations/{slug}", status_code=204, summary="Remove a destination", responses=_PROBLEMS,
               dependencies=[need("settings")])
def delete_destination(slug: str, ctx: Ctx):
    """Its folder and files stay; downloads already asked for still go there."""
    ctx.destinations.delete(slug)
    return Response(status_code=204)


@router.get("/folders", summary="The folders inside one of the media folder's", response_model=schemas.FolderListing,
            responses=_PROBLEMS,
            dependencies=[need("settings")])
def list_folders(ctx: Ctx, path: str = Query("", max_length=300, description="Empty: the media folder itself.")):
    return folders.listing(ctx.config.media_root, path)


@router.get("/folders/check", summary="Whether videos can be written to a folder", response_model=schemas.FolderCheck,
            dependencies=[need("settings")])
def check_folder(ctx: Ctx, path: str = Query(..., max_length=300)):
    return folders.check(ctx.config.media_root, path)
