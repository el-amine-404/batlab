from fastapi import APIRouter

from .. import __version__, schemas, system
from ..context import manager

router = APIRouter(tags=["System"])


@router.get("/system/status", summary="Tool versions, free space and jobs at a glance",
            response_model=schemas.SystemStatus)
def status():
    return {**system.status(manager.settings, manager.list()), "version": __version__}
