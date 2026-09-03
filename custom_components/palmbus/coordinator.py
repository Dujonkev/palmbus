"""Coordinateur de mise à jour pour Palm Bus (GTFS-RT)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from google.transit import gtfs_realtime_pb2
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEPARTURE_PAST_TOLERANCE,
    DOMAIN,
    GTFS_RT_ALERTS_URL,
    GTFS_RT_TRIP_UPDATES_URL,
)
from .gtfs_static import GtfsStaticData, async_get_static_data

_LOGGER = logging.getLogger(__name__)

_FETCH_TIMEOUT = 20


@dataclass
class Departure:
    """Un prochain passage à l'arrêt suivi."""

    trip_id: str
    route_id: str
    line: str
    headsign: str
    color: str | None
    scheduled: datetime | None
    estimated: datetime
    realtime: bool
    delay_minutes: int | None


@dataclass
class Alert:
    """Une perturbation en cours ou à venir sur une ligne suivie."""

    route_id: str | None
    line: str | None
    header: str
    description: str


@dataclass
class PalmBusData:
    """Résultat d'une mise à jour du coordinateur."""

    stop_name: str
    departures: list[Departure] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


class PalmBusCoordinator(DataUpdateCoordinator[PalmBusData]):
    """Interroge le flux GTFS-RT de Palm Bus pour un arrêt donné."""

    def __init__(
        self,
        hass: HomeAssistant,
        static_data: GtfsStaticData,
        *,
        stop_id: str,
        stop_name: str,
        line_filter: list[str] | None,
        max_departures: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{stop_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self.static_data = static_data
        self.stop_id = stop_id
        self.stop_name = stop_name
        self.line_filter = set(line_filter) if line_filter else None
        self.max_departures = max_departures

    async def _async_update_data(self) -> PalmBusData:
        # Les données statiques (noms de lignes, couleurs, destinations) sont
        # partagées entre toutes les entrées configurées et ne sont
        # re-téléchargées que lorsque le cache dépasse STATIC_DATA_MAX_AGE.
        try:
            self.static_data = await async_get_static_data(self.hass)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Rafraîchissement du GTFS statique Palm Bus impossible, "
                "utilisation des données déjà en mémoire",
                exc_info=True,
            )

        try:
            trip_feed = await self._async_fetch_feed(GTFS_RT_TRIP_UPDATES_URL)
        except Exception as err:  # pylint: disable=broad-except
            raise UpdateFailed(
                f"Impossible de récupérer le flux temps réel Palm Bus : {err}"
            ) from err

        try:
            departures = self._extract_departures(trip_feed)
        except Exception as err:  # pylint: disable=broad-except
            raise UpdateFailed(
                f"Réponse GTFS-RT Palm Bus invalide ou inattendue : {err}"
            ) from err

        alerts: list[Alert] = []
        try:
            alert_feed = await self._async_fetch_feed(GTFS_RT_ALERTS_URL)
            alerts = self._extract_alerts(alert_feed)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Flux d'alertes Palm Bus indisponible, ignoré", exc_info=True)

        return PalmBusData(stop_name=self.stop_name, departures=departures, alerts=alerts)

    async def _async_fetch_feed(self, url: str) -> gtfs_realtime_pb2.FeedMessage:
        async with self._session.get(url, timeout=_FETCH_TIMEOUT) as resp:
            resp.raise_for_status()
            content = await resp.read()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(content)
        return feed

    def _extract_departures(self, feed: gtfs_realtime_pb2.FeedMessage) -> list[Departure]:
        now = datetime.now(timezone.utc)
        past_cutoff = now - DEPARTURE_PAST_TOLERANCE
        departures: list[Departure] = []

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            trip_update = entity.trip_update
            trip_desc = trip_update.trip

            if trip_desc.schedule_relationship == trip_desc.CANCELED:
                continue

            trip_info = self.static_data.trip_info(trip_desc.trip_id)
            # `route_id` est optionnel dans GTFS-RT : certains producteurs le
            # laissent vide et s'attendent à ce qu'on le retrouve via
            # `trip_id` dans le GTFS statique (trips.txt).
            route_id = trip_desc.route_id or (trip_info.route_id if trip_info else "")
            if self.line_filter and route_id and route_id not in self.line_filter:
                continue

            for stu in trip_update.stop_time_update:
                if stu.stop_id != self.stop_id:
                    continue
                if stu.schedule_relationship == stu.SKIPPED:
                    continue

                estimated = self._effective_time(stu)
                if estimated is None or estimated < past_cutoff:
                    continue

                route_info = self.static_data.route_info(route_id) if route_id else None

                delay = None
                if stu.HasField("departure") and stu.departure.HasField("delay"):
                    delay = round(stu.departure.delay / 60)
                elif stu.HasField("arrival") and stu.arrival.HasField("delay"):
                    delay = round(stu.arrival.delay / 60)

                departures.append(
                    Departure(
                        trip_id=trip_desc.trip_id,
                        route_id=route_id,
                        line=(route_info.short_name if route_info else route_id) or "?",
                        headsign=(trip_info.headsign if trip_info else "") or "",
                        color=(route_info.color if route_info else None),
                        scheduled=None,
                        estimated=estimated,
                        realtime=True,
                        delay_minutes=delay,
                    )
                )
                break  # un seul horaire par course à cet arrêt

        departures.sort(key=lambda dep: dep.estimated)
        return departures[: self.max_departures]

    @staticmethod
    def _effective_time(stu) -> datetime | None:
        """Détermine l'heure de passage effective (départ, sinon arrivée)."""
        epoch: int | None = None
        if stu.HasField("departure") and stu.departure.HasField("time"):
            epoch = stu.departure.time
        elif stu.HasField("arrival") and stu.arrival.HasField("time"):
            epoch = stu.arrival.time
        if not epoch:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    def _extract_alerts(self, feed: gtfs_realtime_pb2.FeedMessage) -> list[Alert]:
        alerts: list[Alert] = []
        for entity in feed.entity:
            if not entity.HasField("alert"):
                continue
            alert = entity.alert

            concerned_route: str | None = None
            concerns_stop = False
            for informed in alert.informed_entity:
                if informed.stop_id and informed.stop_id == self.stop_id:
                    concerns_stop = True
                if informed.route_id:
                    if self.line_filter and informed.route_id not in self.line_filter:
                        continue
                    concerned_route = informed.route_id

            if not concerns_stop and concerned_route is None:
                continue

            header = _best_translation(alert.header_text)
            description = _best_translation(alert.description_text)
            if not header and not description:
                continue

            route_info = (
                self.static_data.route_info(concerned_route) if concerned_route else None
            )
            alerts.append(
                Alert(
                    route_id=concerned_route,
                    line=route_info.short_name if route_info else None,
                    header=header,
                    description=description,
                )
            )
        return alerts


def _best_translation(translated_string) -> str:
    """Choisit la traduction française si disponible, sinon la première."""
    fr_text = ""
    fallback = ""
    for translation in translated_string.translation:
        if not fallback:
            fallback = translation.text
        if translation.language in ("fr", "fr-FR"):
            fr_text = translation.text
            break
    return fr_text or fallback
