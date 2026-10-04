"""Starts the local bridge API (for the Hermes plugin) and the public sign-up site."""

import asyncio
import logging
from functools import partial

import uvicorn

from assistant.bridge.server import create_bridge_app
from assistant.bridge.service import ToolService
from assistant.config.settings import load_settings
from assistant.messaging.photon_users import PhotonUsers
from assistant.storage.db import connect
from assistant.storage.memories import MemoryRepo
from assistant.storage.pending_changes import PendingChangeRepo
from assistant.storage.users import UserRepo
from assistant.tools import google_auth, maps_service, places_service
from assistant.web.app import create_web_app

log = logging.getLogger(__name__)


def build_servers() -> list[uvicorn.Server]:
    settings = load_settings()
    travel_times = places = None
    if settings.google_maps_api_key:
        travel_times = maps_service.GoogleRoutesClient(settings.google_maps_api_key)
        places = places_service.GooglePlacesClient(settings.google_maps_api_key)
    else:
        log.warning("No GOOGLE_MAPS_API_KEY; plan_departure and find_places will report that they aren't set up.")
    bridge_conn = connect(settings.db_path)
    service = ToolService(
        UserRepo(bridge_conn, settings.secret_key),
        settings.public_base_url,
        travel_times=travel_times,
        memories=MemoryRepo(bridge_conn, settings.secret_key),
        pending_changes=PendingChangeRepo(bridge_conn),
        places=places,
    )
    bridge = uvicorn.Config(
        create_bridge_app(service, settings.bridge_token), host="127.0.0.1", port=settings.bridge_port, log_level="info"
    )

    photon = None
    if settings.photon_project_id and settings.photon_project_secret:
        photon = PhotonUsers(settings.photon_project_id, settings.photon_project_secret)
    else:
        log.warning("No Photon project credentials found; the sign-up site won't register phone numbers.")

    site = create_web_app(
        UserRepo(connect(settings.db_path), settings.secret_key),
        public_base_url=settings.public_base_url,
        bot_phone=settings.bot_phone,
        default_timezone=settings.timezone,
        start_google_login=partial(google_auth.authorization_url, settings.google_web_client_path),
        finish_google_login=partial(google_auth.exchange_code, settings.google_web_client_path),
        register_phone=photon.register if photon else None,
    )
    # Only this port is exposed through ngrok; ngrok reaches it on localhost.
    web = uvicorn.Config(site, host="127.0.0.1", port=settings.web_port, log_level="info")
    return [uvicorn.Server(bridge), uvicorn.Server(web)]


async def serve() -> None:
    await asyncio.gather(*(server.serve() for server in build_servers()))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
