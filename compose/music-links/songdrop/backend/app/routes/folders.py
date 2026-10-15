from fastapi import APIRouter, Query

from .. import folders, schemas
from ..context import manager

router = APIRouter(tags=["Folders"])


@router.get("/folders", summary="The folders inside one under the output root, for choosing the output folder",
            response_model=schemas.FolderListing, responses={400: {"model": schemas.Problem}})
def list_folders(path: str = Query("", description="Relative to the output root; empty: the root.")):
    return folders.listing(manager.settings.output_root, path)


@router.get("/folders/check", summary="Whether songs can be written to a folder",
            response_model=schemas.FolderCheck)
def check_folder(path: str = Query(description="Relative to the output root.")):
    return folders.check(manager.settings.output_root, path)
