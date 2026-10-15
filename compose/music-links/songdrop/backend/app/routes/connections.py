from fastapi import APIRouter, Response

from .. import events, schemas
from ..connections import TYPES
from ..connections.store import ConnectionIn
from ..context import connections

router = APIRouter(tags=["connections"])
_PROBLEMS = {404: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


@router.get("/connections/types", summary="The connection types, their fields, and the events to choose from",
            response_model=schemas.ConnectionTypes)
def connection_types():
    return {"types": [kind.describe() for kind in TYPES.values()],
            "events": [vars(item) for item in events.CATALOG]}


@router.get("/connections", summary="Where songdrop's events go", response_model=schemas.ConnectionList)
def list_connections():
    return {"connections": connections.list()}


@router.post("/connections", status_code=201, summary="Add a connection", response_model=schemas.Connection,
             responses=_PROBLEMS)
def create_connection(body: ConnectionIn):
    return connections.create(body.model_dump())


@router.post("/connections/test", summary="Send a test message with a connection not saved yet",
             response_model=schemas.TestResult, responses=_PROBLEMS)
def test_new_connection(body: ConnectionIn):
    return connections.test(body.model_dump())


@router.get("/connections/{connection_id}", summary="One connection", response_model=schemas.Connection,
            responses=_PROBLEMS)
def get_connection(connection_id: str):
    return connections.get(connection_id)


@router.put("/connections/{connection_id}", summary="Change a connection; a secret left empty keeps its value",
            response_model=schemas.Connection, responses=_PROBLEMS)
def update_connection(connection_id: str, body: ConnectionIn):
    return connections.update(connection_id, body.model_dump())


@router.post("/connections/{connection_id}/test",
             summary="Send a test message with a saved connection, changed as the body says",
             response_model=schemas.TestResult, responses=_PROBLEMS)
def test_connection(connection_id: str, body: ConnectionIn):
    return connections.test(body.model_dump(), connection_id)


@router.delete("/connections/{connection_id}", status_code=204, summary="Remove a connection",
               responses=_PROBLEMS)
def delete_connection(connection_id: str):
    connections.delete(connection_id)
    return Response(status_code=204)
