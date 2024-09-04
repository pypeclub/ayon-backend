from fastapi import Response

from ayon_server.addons.library import AddonLibrary
from ayon_server.types import Field, OPModel

from .router import router


class AddonLoadRequest(OPModel):
    name: str = Field(..., description="The name of the addon to load")
    version: str = Field(..., description="The version of the addon to load")
    dir: str = Field(..., description="The directory of the addon to load")


@router.post("/addonHotLoad", tags=["System"])
async def hot_load_addon(request: AddonLoadRequest):
    await AddonLibrary.hot_load(request.name, request.version, request.dir)
    return Response(status_code=204)
