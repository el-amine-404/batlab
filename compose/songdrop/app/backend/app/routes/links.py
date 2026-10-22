from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import __version__, detect, options, schemas
from ..auth import need
from ..context import manager

# Anyone may: whether songdrop is up.
public = APIRouter()
router = APIRouter()


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=detect.MAX_URL_LENGTH, description="The link to download.")


@public.get("/health", tags=["Service"], summary="Whether songdrop is up", response_model=schemas.Health)
def health():
    return {"ok": True, "version": __version__}


@router.get("/options", tags=["Links"], summary="The download options of each engine, with their defaults",
            response_model=schemas.OptionsSpec)
def get_options():
    return options.describe(manager.settings.defaults)


@router.post("/detect", tags=["Links"], summary="Which engine would take a link, without downloading",
             response_model=schemas.Detection, response_model_exclude_none=True, dependencies=[need("download")])
def post_detect(body: LinkIn):
    try:
        return {"supported": True, **detect.detect(body.url).as_dict()}
    except detect.UnsupportedLink as exc:
        return {"supported": False, "message": str(exc)}
