from fastapi import APIRouter

from .. import schemas, system
from ..context import manager

router = APIRouter(tags=["system"])


@router.get("/system/status", summary="Tool versions, free space and jobs at a glance",
            response_model=schemas.SystemStatus)
def status():
    return system.status(manager.settings, manager.list())
