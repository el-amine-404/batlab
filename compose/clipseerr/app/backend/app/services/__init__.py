"""Services: the apps requests go to (Settings, Services), in order."""

from .aurral import Aurral
from .base import Context, Kind, ServiceType, Update
from .songdrop import Songdrop
from .videodrop import Videodrop
from .webhook import Webhook

TYPES: dict[str, ServiceType] = {kind.key: kind for kind in (Aurral(), Songdrop(), Videodrop(), Webhook())}


def describe_extra(kind: ServiceType, config: dict) -> dict:
    """What the API adds to a saved service: the kinds it takes."""
    try:
        return {"kinds": list(kind.kinds(config))}
    except Exception:
        return {"kinds": []}


__all__ = ["TYPES", "Context", "Kind", "ServiceType", "Update", "describe_extra"]
