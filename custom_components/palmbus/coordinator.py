"""Coordinateur de mise à jour pour Palm Bus (GTFS + GTFS-RT)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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
    LATE_BUS_WINDOW,
    MAX_KEPT_DEPARTURES,
    SCHEDULE_HORIZON,
)
from .gtfs_static import GtfsStaticData, StopTime, async_get_static_data

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
    # Les `max_departures` prochains passages (toutes lignes suivies confondues).
    departures: list[Departure] = field(default_factory=list)
    # Liste plus longue, utilisée par les capteurs « par ligne ».
    all_departures: list[Departure] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    realtime_available: bool = True


@dataclass
class _RealtimeTrip:
    """Ce que le flux temps réel dit d'une course, vu depuis l'arrêt suivi."""

    canceled: bool = False
    # Horaire estimé à l'arrêt suivi, s'il figure dans le flux.
    estimated: datetime | None = None
    delay_seconds: int | None = None
    skipped: bool = False
    # Mises à jour pour d'autres arrêts : (stop_sequence, retard en s).
    other_updates: list[tuple[int | None, int | None]] = field(default_factory=list)


class PalmBusCoordinator(DataUpdateCoordinator[PalmBusData]):
    """Combine horaires théoriques (GTFS) et temps réel (GTFS-RT) pour un arrêt."""

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
        # Les données statiques (noms de lignes, couleurs, destinations,
        # horaires) sont partagées entre toutes les entrées configurées et ne
        # sont re-téléchargées que lorsque le cache dépasse STATIC_DATA_MAX_AGE.
        try:
            self.static_data = await async_get_static_data(self.hass)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Rafraîchissement du GTFS statique Palm Bus impossible, "
                "utilisation des données déjà en mémoire",
                exc_info=True,
            )

        stop_times: list[StopTime] = []
        try:
            stop_times = await self.static_data.async_load_stop_times(self.stop_id)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Horaires théoriques indisponibles pour l'arrêt %s", self.stop_id, exc_info=True
            )

        trip_feed: gtfs_realtime_pb2.FeedMessage | None = None
        try:
            trip_feed = await self._async_fetch_feed(GTFS_RT_TRIP_UPDATES_URL)
        except Exception as err:  # pylint: disable=broad-except
            if not stop_times:
                raise UpdateFailed(
                    f"Impossible de récupérer le flux temps réel Palm Bus : {err}"
                ) from err
            _LOGGER.debug(
                "Flux temps réel Palm Bus indisponible, horaires théoriques seuls : %s", err
            )

        try:
            all_departures = self._build_departures(trip_feed, stop_times)
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

        return PalmBusData(
            stop_name=self.stop_name,
            departures=all_departures[: self.max_departures],
            all_departures=all_departures,
            alerts=alerts,
            realtime_available=trip_feed is not None,
        )

    async def _async_fetch_feed(self, url: str) -> gtfs_realtime_pb2.FeedMessage:
        async with self._session.get(url, timeout=_FETCH_TIMEOUT) as resp:
            resp.raise_for_status()
            content = await resp.read()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(content)
        return feed

    # -- Construction de la liste des passages --------------------------------

    def _route_for_trip(self, trip_id: str, rt_route_id: str = "") -> str:
        # `route_id` est optionnel dans GTFS-RT : certains producteurs le
        # laissent vide et s'attendent à ce qu'on le retrouve via `trip_id`
        # dans le GTFS statique (trips.txt).
        trip_info = self.static_data.trip_info(trip_id)
        return rt_route_id or (trip_info.route_id if trip_info else "")

    def _make_departure(
        self,
        trip_id: str,
        route_id: str,
        *,
        scheduled: datetime | None,
        estimated: datetime,
        realtime: bool,
        delay_minutes: int | None,
    ) -> Departure:
        trip_info = self.static_data.trip_info(trip_id)
        route_info = self.static_data.route_info(route_id) if route_id else None
        return Departure(
            trip_id=trip_id,
            route_id=route_id,
            line=(route_info.short_name if route_info else route_id) or "?",
            headsign=(trip_info.headsign if trip_info else "") or "",
            color=(route_info.color if route_info else None),
            scheduled=scheduled,
            estimated=estimated,
            realtime=realtime,
            delay_minutes=delay_minutes,
        )

    def _parse_realtime(
        self, feed: gtfs_realtime_pb2.FeedMessage
    ) -> dict[str, tuple[str, _RealtimeTrip]]:
        """trip_id -> (route_id, infos temps réel pour l'arrêt suivi)."""
        trips: dict[str, tuple[str, _RealtimeTrip]] = {}
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            trip_update = entity.trip_update
            trip_desc = trip_update.trip
            trip_id = trip_desc.trip_id
            if not trip_id:
                continue
            info = _RealtimeTrip(canceled=trip_desc.schedule_relationship == trip_desc.CANCELED)
            for stu in trip_update.stop_time_update:
                sequence = stu.stop_sequence if stu.HasField("stop_sequence") else None
                delay = _stu_delay(stu)
                if stu.stop_id == self.stop_id and info.estimated is None and not info.skipped:
                    if stu.schedule_relationship == stu.SKIPPED:
                        info.skipped = True
                        continue
                    info.estimated = _effective_time(stu)
                    info.delay_seconds = delay
                else:
                    info.other_updates.append((sequence, delay))
            trips[trip_id] = (self._route_for_trip(trip_id, trip_desc.route_id), info)
        return trips

    def _build_departures(
        self,
        feed: gtfs_realtime_pb2.FeedMessage | None,
        stop_times: list[StopTime],
    ) -> list[Departure]:
        now = datetime.now(timezone.utc)
        past_cutoff = now - DEPARTURE_PAST_TOLERANCE
        realtime = self._parse_realtime(feed) if feed is not None else {}
        departures: list[Departure] = []
        handled: set[str] = set()

        def wanted(route_id: str) -> bool:
            return not (self.line_filter and route_id and route_id not in self.line_filter)

        # 1) Horaires théoriques, corrigés par le temps réel quand il existe.
        for stop_time, scheduled in self.static_data.scheduled_departures(
            self.stop_id, now - LATE_BUS_WINDOW, now + SCHEDULE_HORIZON
        ):
            trip_id = stop_time.trip_id
            if trip_id in handled:
                continue  # course qui repasse deux fois à l'arrêt : premier passage
            route_id = self._route_for_trip(trip_id)
            rt = realtime.get(trip_id)
            if rt is not None:
                route_id = rt[0] or route_id
            if not wanted(route_id):
                continue

            if rt is None:
                if scheduled < past_cutoff:
                    continue
                handled.add(trip_id)
                departures.append(
                    self._make_departure(
                        trip_id,
                        route_id,
                        scheduled=scheduled,
                        estimated=scheduled,
                        realtime=False,
                        delay_minutes=None,
                    )
                )
                continue

            info = rt[1]
            handled.add(trip_id)
            if info.canceled or info.skipped:
                continue
            if info.estimated is not None:
                estimated = info.estimated
                delay = info.delay_seconds
                if delay is None:
                    delay = int((estimated - scheduled).total_seconds())
            else:
                delay = _propagated_delay(info.other_updates, stop_time.stop_sequence)
                if delay is _BUS_PASSED:
                    continue
                if delay is None:
                    # Course présente dans le flux mais sans information
                    # exploitable pour cet arrêt : horaire théorique.
                    if scheduled < past_cutoff:
                        continue
                    departures.append(
                        self._make_departure(
                            trip_id,
                            route_id,
                            scheduled=scheduled,
                            estimated=scheduled,
                            realtime=False,
                            delay_minutes=None,
                        )
                    )
                    continue
                estimated = scheduled + timedelta(seconds=delay)
            if estimated < past_cutoff:
                continue
            departures.append(
                self._make_departure(
                    trip_id,
                    route_id,
                    scheduled=scheduled,
                    estimated=estimated,
                    realtime=True,
                    delay_minutes=round(delay / 60),
                )
            )

        # 2) Courses temps réel absentes des horaires théoriques (courses
        #    ajoutées, GTFS statique pas encore à jour, etc.).
        for trip_id, (route_id, info) in realtime.items():
            if trip_id in handled or info.canceled or info.skipped or info.estimated is None:
                continue
            if info.estimated < past_cutoff or not wanted(route_id):
                continue
            departures.append(
                self._make_departure(
                    trip_id,
                    route_id,
                    scheduled=None,
                    estimated=info.estimated,
                    realtime=True,
                    delay_minutes=(
                        round(info.delay_seconds / 60) if info.delay_seconds is not None else None
                    ),
                )
            )

        departures.sort(key=lambda dep: dep.estimated)
        return departures[:MAX_KEPT_DEPARTURES]

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


_BUS_PASSED = object()


def _stu_delay(stu) -> int | None:
    """Retard (en secondes) annoncé par une mise à jour d'arrêt."""
    if stu.HasField("departure") and stu.departure.HasField("delay"):
        return stu.departure.delay
    if stu.HasField("arrival") and stu.arrival.HasField("delay"):
        return stu.arrival.delay
    return None


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


def _propagated_delay(
    updates: list[tuple[int | None, int | None]], our_sequence: int | None
):
    """Retard à appliquer à notre arrêt d'après les arrêts voisins (règle GTFS-RT).

    - Retourne `_BUS_PASSED` si toutes les mises à jour concernent des arrêts
      situés après le nôtre : le bus est déjà passé.
    - Retourne le retard de la dernière mise à jour située avant notre arrêt
      (le retard se propage aux arrêts suivants).
    - Retourne None si on ne peut rien conclure.
    """
    if our_sequence is None:
        return None
    known = [(seq, delay) for seq, delay in updates if seq is not None]
    if not known:
        return None
    before = [(seq, delay) for seq, delay in known if seq < our_sequence]
    if not before:
        return _BUS_PASSED
    return max(before, key=lambda item: item[0])[1]


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
