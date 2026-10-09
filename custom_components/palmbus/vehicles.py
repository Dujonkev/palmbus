"""Positions des bus Palm Bus en temps réel (flux GTFS-RT « vehicle positions »)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time

from google.transit import gtfs_realtime_pb2
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, GTFS_RT_VEHICLE_POSITIONS_URL, VEHICLES_SCAN_INTERVAL
from .gtfs_static import GtfsStaticData, async_get_static_data

_LOGGER = logging.getLogger(__name__)

_FETCH_TIMEOUT = 20

_STATUS_LABELS = {
    0: "Arrive à",  # INCOMING_AT
    1: "À l'arrêt",  # STOPPED_AT
    2: "En route vers",  # IN_TRANSIT_TO
}


@dataclass
class Vehicle:
    """Un bus en circulation."""

    vehicle_id: str
    label: str
    latitude: float
    longitude: float
    bearing: float | None
    speed_kmh: float | None
    route_id: str
    line: str
    color: str | None
    text_color: str | None
    headsign: str
    status: str | None
    stop_name: str | None
    reported_at: datetime | None
    received: float  # time.monotonic() de la dernière réception


class PalmBusVehiclesCoordinator(DataUpdateCoordinator[dict[str, Vehicle]]):
    """Interroge le flux des positions et garde la dernière position de chaque bus."""

    def __init__(
        self,
        hass: HomeAssistant,
        static_data: GtfsStaticData,
        *,
        line_filter: list[str] | None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_vehicles",
            update_interval=VEHICLES_SCAN_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self.static_data = static_data
        self.line_filter = set(line_filter) if line_filter else None
        self._vehicles: dict[str, Vehicle] = {}

    async def _async_update_data(self) -> dict[str, Vehicle]:
        try:
            self.static_data = await async_get_static_data(self.hass)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("GTFS statique non rafraîchi, données en mémoire utilisées", exc_info=True)

        try:
            async with self._session.get(GTFS_RT_VEHICLE_POSITIONS_URL, timeout=_FETCH_TIMEOUT) as resp:
                resp.raise_for_status()
                content = await resp.read()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(content)
        except Exception as err:  # pylint: disable=broad-except
            raise UpdateFailed(f"Positions des bus Palm Bus indisponibles : {err}") from err

        now = time.monotonic()
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vehicle = self._parse(entity.vehicle, now)
            if vehicle is None:
                continue
            if self.line_filter and vehicle.route_id not in self.line_filter:
                self._vehicles.pop(vehicle.vehicle_id, None)
                continue
            self._vehicles[vehicle.vehicle_id] = vehicle
        # On garde les bus absents de ce flux : les entités les rendent
        # indisponibles (donc invisibles sur la carte) après VEHICLE_STALE_AFTER.
        return dict(self._vehicles)

    def _parse(self, pos, now: float) -> Vehicle | None:
        if not pos.HasField("position"):
            return None
        vehicle_id = pos.vehicle.id or pos.vehicle.label
        if not vehicle_id:
            return None

        trip_id = pos.trip.trip_id if pos.HasField("trip") else ""
        trip_info = self.static_data.trip_info(trip_id) if trip_id else None
        route_id = (pos.trip.route_id if pos.HasField("trip") else "") or (
            trip_info.route_id if trip_info else ""
        )
        route = self.static_data.route_info(route_id) if route_id else None

        position = pos.position
        speed = position.speed * 3.6 if position.HasField("speed") else None
        bearing = position.bearing if position.HasField("bearing") else None
        status = _STATUS_LABELS.get(pos.current_status) if pos.HasField("current_status") else None
        stop_name = self.static_data.stop_name(pos.stop_id) if pos.stop_id else None
        reported = (
            datetime.fromtimestamp(pos.timestamp, tz=timezone.utc) if pos.timestamp else None
        )

        return Vehicle(
            vehicle_id=vehicle_id,
            label=pos.vehicle.label or vehicle_id,
            latitude=position.latitude,
            longitude=position.longitude,
            bearing=bearing,
            speed_kmh=round(speed, 1) if speed is not None else None,
            route_id=route_id,
            line=(route.short_name if route else route_id) or "?",
            color=route.color if route else None,
            text_color=route.text_color if route else None,
            headsign=(trip_info.headsign if trip_info else "") or "",
            status=status,
            stop_name=stop_name,
            reported_at=reported,
            received=now,
        )
