from fastapi import APIRouter, Query

from .. import schemas
from ..context import engine

router = APIRouter(tags=["Hooks"])


@router.post("/hooks/songdrop", summary="songdrop's callback: a job of one of clipseerr's requests changed",
             description="songdrop's callback_url (its API's /jobs). Only wakes the engine: it asks songdrop "
                         "itself, so a forged call changes nothing.", response_model=schemas.Health)
def songdrop_hook(request: str = Query("", max_length=40)):
    engine.wake()
    return {"ok": True}


@router.post("/hooks/videodrop", summary="videodrop's callback: a download of one of clipseerr's requests ended",
             description="videodrop's callback_url (its API's /jobs). Only wakes the engine: it asks videodrop "
                         "itself, so a forged call changes nothing.", response_model=schemas.Health)
def videodrop_hook(request: str = Query("", max_length=40)):
    engine.wake()
    return {"ok": True}
