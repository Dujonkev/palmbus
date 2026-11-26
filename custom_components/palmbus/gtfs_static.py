"""Téléchargement et analyse du GTFS statique de Palm Bus.

Le fichier GTFS statique sert à :
- rechercher un arrêt par son nom (config flow) ;
- lister les lignes qui desservent un arrêt donné (config flow) ;
- retrouver le nom court d'une ligne, sa couleur et la destination
  (trip_headsign) d'une course pour l'affichage ;
- calculer les horaires théoriques d'un arrêt suivi, utilisés en secours
  quand une course n'est pas (ou plus) présente dans le flux temps réel.

stop_times.txt compte plus d'un million de lignes sur ce réseau : il n'est
jamais chargé en entier. Pour chaque arrêt suivi, on n'en extrait qu'une
fois (par version du GTFS) les quelques milliers de lignes qui le
concernent, dans un executor.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, MAX_SEARCH_RESULTS, STATIC_DATA_MAX_AGE, STATIC_GTFS_URL, TIMEZONE

_LOGGER = logging.getLogger(__name__)

# Le zip GTFS (~15 Mo) est mis en cache dans <config>/palmbus/ et non dans
# .storage/, qui est réservé aux stores JSON de Home Assistant et inclus dans
# chaque sauvegarde.
_CACHE_DIRNAME = DOMAIN
_CACHE_FILENAME = "gtfs_static.zip"
_LEGACY_CACHE_FILENAME = "palmbus_gtfs_static.zip"
_HASS_DATA_KEY = "static_gtfs"
_LOCK_KEY = "static_gtfs_lock"

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90)


@dataclass
class StopInfo:
    """Un arrêt du réseau."""

    stop_id: str
    name: str
    lat: float | None = None
    lon: float | None = None


@dataclass
class RouteInfo:
    """Une ligne du réseau."""

    route_id: str
    short_name: str
    long_name: str
    color: str | None = None
    text_color: str | None = None


@dataclass
class TripInfo:
    """Une course (trip) du réseau."""

    route_id: str
    headsign: str
    service_id: str = ""


@dataclass(frozen=True)
class StopTime:
    """Un passage théorique d'une course à l'arrêt suivi."""

    trip_id: str
    seconds: int  # secondes depuis « midi moins 12 h » du jour de service (peut dépasser 24 h)
    stop_sequence: int | None


@dataclass
class ServiceCalendar:
    """Une ligne de calendar.txt."""

    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]  # lundi → dimanche
    start: date
    end: date


@dataclass
class GtfsStaticData:
    """Contenu utile du GTFS statique, une fois analysé."""

    stops: dict[str, StopInfo] = field(default_factory=dict)
    routes: dict[str, RouteInfo] = field(default_factory=dict)
    trips: dict[str, TripInfo] = field(default_factory=dict)
    calendar: dict[str, ServiceCalendar] = field(default_factory=dict)
    # service_id -> {date: True (ajouté) / False (supprimé)}
    calendar_dates: dict[str, dict[date, bool]] = field(default_factory=dict)
    fetched_at: float = 0.0
    _zip_path: str | None = field(default=None, repr=False)
    _trip_ids_cache: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _stop_times_cache: dict[str, list[StopTime]] = field(default_factory=dict, repr=False)

    # -- Chargement -----------------------------------------------------

    @classmethod
    async def async_load(
        cls,
        session: aiohttp.ClientSession,
        cache_path: str,
        *,
        force_refresh: bool = False,
    ) -> "GtfsStaticData":
        """Charge le GTFS statique, en réutilisant le cache disque si récent."""
        loop = asyncio.get_running_loop()
        cache_exists = await loop.run_in_executor(None, os.path.exists, cache_path)
        need_download = force_refresh or not cache_exists
        if not need_download:
            mtime = await loop.run_in_executor(None, os.path.getmtime, cache_path)
            age = time.time() - mtime
            need_download = age > STATIC_DATA_MAX_AGE.total_seconds()

        if need_download:
            try:
                await cls._async_download(session, cache_path)
            except Exception:  # pylint: disable=broad-except
                cache_exists = await loop.run_in_executor(None, os.path.exists, cache_path)
                if cache_exists:
                    _LOGGER.warning(
                        "Téléchargement du GTFS statique Palm Bus impossible, "
                        "utilisation du cache local existant"
                    )
                else:
                    raise

        fetched_at = await loop.run_in_executor(None, os.path.getmtime, cache_path)
        data = cls(_zip_path=cache_path, fetched_at=fetched_at)
        await data._async_parse_light_tables()
        return data

    @staticmethod
    async def _async_download(session: aiohttp.ClientSession, cache_path: str) -> None:
        _LOGGER.debug("Téléchargement du GTFS statique Palm Bus")
        async with session.get(STATIC_GTFS_URL, timeout=_REQUEST_TIMEOUT) as resp:
            resp.raise_for_status()
            content = await resp.read()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, GtfsStaticData._write_cache_sync, cache_path, content)

    @staticmethod
    def _write_cache_sync(cache_path: str, content: bytes) -> None:
        """Écrit le zip GTFS sur disque (appelé dans un executor, hors boucle asyncio)."""
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp_path = f"{cache_path}.tmp"
        with open(tmp_path, "wb") as file:
            file.write(content)
        os.replace(tmp_path, cache_path)

    async def _async_parse_light_tables(self) -> None:
        """Analyse stops, routes, trips et calendriers (tables raisonnables)."""
        loop = asyncio.get_running_loop()
        stops, routes, trips, calendar, calendar_dates = await loop.run_in_executor(
            None, self._parse_light_tables_sync, self._zip_path
        )
        self.stops = stops
        self.routes = routes
        self.trips = trips
        self.calendar = calendar
        self.calendar_dates = calendar_dates

    @staticmethod
    def _parse_light_tables_sync(
        zip_path: str,
    ) -> tuple[
        dict[str, StopInfo],
        dict[str, RouteInfo],
        dict[str, TripInfo],
        dict[str, ServiceCalendar],
        dict[str, dict[date, bool]],
    ]:
        stops: dict[str, StopInfo] = {}
        routes: dict[str, RouteInfo] = {}
        trips: dict[str, TripInfo] = {}
        calendar: dict[str, ServiceCalendar] = {}
        calendar_dates: dict[str, dict[date, bool]] = {}

        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())

            # calendar.txt et calendar_dates.txt sont tous deux facultatifs
            # dans la norme GTFS (au moins l'un des deux est présent).
            if "calendar.txt" in names:
                with archive.open("calendar.txt") as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                        service_id = row.get("service_id")
                        start = _parse_gtfs_date(row.get("start_date"))
                        end = _parse_gtfs_date(row.get("end_date"))
                        if not service_id or start is None or end is None:
                            continue
                        calendar[service_id] = ServiceCalendar(
                            weekdays=tuple(
                                (row.get(day) or "0").strip() == "1" for day in _WEEKDAYS
                            ),
                            start=start,
                            end=end,
                        )

            if "calendar_dates.txt" in names:
                with archive.open("calendar_dates.txt") as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                        service_id = row.get("service_id")
                        day = _parse_gtfs_date(row.get("date"))
                        exception = (row.get("exception_type") or "").strip()
                        if not service_id or day is None or exception not in ("1", "2"):
                            continue
                        calendar_dates.setdefault(service_id, {})[day] = exception == "1"

            with archive.open("stops.txt") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    stop_id = row.get("stop_id")
                    if not stop_id:
                        continue
                    try:
                        lat = float(row["stop_lat"]) if row.get("stop_lat") else None
                        lon = float(row["stop_lon"]) if row.get("stop_lon") else None
                    except ValueError:
                        lat = lon = None
                    stops[stop_id] = StopInfo(
                        stop_id=stop_id,
                        name=row.get("stop_name", stop_id),
                        lat=lat,
                        lon=lon,
                    )

            with archive.open("routes.txt") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    route_id = row.get("route_id")
                    if not route_id:
                        continue
                    routes[route_id] = RouteInfo(
                        route_id=route_id,
                        short_name=row.get("route_short_name") or route_id,
                        long_name=row.get("route_long_name") or "",
                        color=row.get("route_color") or None,
                        text_color=row.get("route_text_color") or None,
                    )

            with archive.open("trips.txt") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    trip_id = row.get("trip_id")
                    if not trip_id:
                        continue
                    trips[trip_id] = TripInfo(
                        route_id=row.get("route_id", ""),
                        headsign=row.get("trip_headsign") or "",
                        service_id=row.get("service_id") or "",
                    )

        return stops, routes, trips, calendar, calendar_dates

    # -- Recherche --------------------------------------------------------

    def search_stops(self, query: str) -> list[StopInfo]:
        """Recherche des arrêts dont le nom contient `query`."""
        query_norm = _normalize(query)
        if not query_norm:
            return []

        matches: list[StopInfo] = []
        seen_names: set[tuple[str, str]] = set()
        for stop in self.stops.values():
            if query_norm in _normalize(stop.name):
                key = (stop.name, f"{stop.lat:.4f}" if stop.lat else "")
                if key in seen_names:
                    continue
                seen_names.add(key)
                matches.append(stop)
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        matches.sort(key=lambda s: s.name)
        return matches

    def routes_for_stop_id(self, stop_id: str) -> list[RouteInfo]:
        """Retourne les lignes desservant un arrêt donné.

        Nécessite d'avoir parcouru stop_times.txt au préalable via
        `async_load_routes_for_stop`. Combine le résultat avec les tables
        trips/routes déjà chargées.
        """
        route_ids: set[str] = set()
        for trip_id in self._trip_ids_cache.get(stop_id, ()):
            trip = self.trips.get(trip_id)
            if trip and trip.route_id:
                route_ids.add(trip.route_id)
        return sorted(
            (self.routes.get(rid) or RouteInfo(rid, rid, "") for rid in route_ids),
            key=lambda r: _line_sort_key(r.short_name),
        )

    async def async_load_routes_for_stop(self, stop_id: str) -> list[RouteInfo]:
        """Parcourt stop_times.txt (une seule fois) pour trouver les lignes d'un arrêt."""
        await self.async_load_stop_times(stop_id)
        return self.routes_for_stop_id(stop_id)

    async def async_load_stop_times(self, stop_id: str) -> list[StopTime]:
        """Horaires théoriques d'un arrêt, extraits une seule fois de stop_times.txt."""
        if stop_id not in self._stop_times_cache:
            loop = asyncio.get_running_loop()
            stop_times = await loop.run_in_executor(
                None, self._stop_times_for_stop_sync, self._zip_path, stop_id
            )
            self._stop_times_cache[stop_id] = stop_times
            self._trip_ids_cache[stop_id] = {st.trip_id for st in stop_times}
        return self._stop_times_cache[stop_id]

    @staticmethod
    def _stop_times_for_stop_sync(zip_path: str, stop_id: str) -> list[StopTime]:
        stop_times: list[StopTime] = []
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open("stop_times.txt") as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                header = next(reader)
                try:
                    stop_idx = header.index("stop_id")
                    trip_idx = header.index("trip_id")
                except ValueError:
                    return stop_times
                dep_idx = header.index("departure_time") if "departure_time" in header else -1
                arr_idx = header.index("arrival_time") if "arrival_time" in header else -1
                seq_idx = header.index("stop_sequence") if "stop_sequence" in header else -1
                for row in reader:
                    if len(row) <= stop_idx or row[stop_idx] != stop_id:
                        continue
                    seconds = None
                    for idx in (dep_idx, arr_idx):
                        if 0 <= idx < len(row):
                            seconds = _parse_gtfs_time(row[idx])
                            if seconds is not None:
                                break
                    sequence = None
                    if 0 <= seq_idx < len(row):
                        try:
                            sequence = int(row[seq_idx])
                        except ValueError:
                            sequence = None
                    if seconds is None:
                        # Passage sans horaire (arrêt « interpolé ») : on le
                        # garde pour la liste des lignes, avec un horaire nul
                        # qui sera ignoré par scheduled_departures().
                        seconds = -1
                    stop_times.append(StopTime(row[trip_idx], seconds, sequence))
        return stop_times

    def cached_stop_times(self, stop_id: str) -> list[StopTime]:
        """Horaires déjà extraits pour cet arrêt (liste vide sinon)."""
        return self._stop_times_cache.get(stop_id, [])

    # -- Horaires théoriques ------------------------------------------------

    def is_service_active(self, service_id: str, day: date) -> bool:
        """Indique si un service (calendar / calendar_dates) circule ce jour-là."""
        exceptions = self.calendar_dates.get(service_id)
        if exceptions is not None and day in exceptions:
            return exceptions[day]
        cal = self.calendar.get(service_id)
        if cal is None:
            return False
        return cal.start <= day <= cal.end and cal.weekdays[day.weekday()]

    def scheduled_departures(
        self, stop_id: str, start: datetime, end: datetime
    ) -> list[tuple[StopTime, datetime]]:
        """Passages théoriques (en UTC) à l'arrêt entre `start` et `end` (datetimes avec fuseau).

        Nécessite `async_load_stop_times(stop_id)` au préalable. Les horaires
        GTFS peuvent dépasser 24:00:00 (courses après minuit rattachées au
        jour de service de la veille), d'où la prise en compte de la veille.
        """
        stop_times = self._stop_times_cache.get(stop_id)
        if not stop_times:
            return []

        tz = ZoneInfo(TIMEZONE)
        first_day = start.astimezone(tz).date() - timedelta(days=1)
        last_day = end.astimezone(tz).date()
        results: list[tuple[StopTime, datetime]] = []

        day = first_day
        while day <= last_day:
            active: dict[str, bool] = {}
            # Référence GTFS : « midi moins 12 h », correcte les jours de
            # changement d'heure.
            reference = datetime(day.year, day.month, day.day, 12, tzinfo=tz) - timedelta(
                hours=12
            )
            for stop_time in stop_times:
                if stop_time.seconds < 0:
                    continue
                trip = self.trips.get(stop_time.trip_id)
                if trip is None:
                    continue
                if trip.service_id not in active:
                    active[trip.service_id] = self.is_service_active(trip.service_id, day)
                if not active[trip.service_id]:
                    continue
                when = (reference + timedelta(seconds=stop_time.seconds)).astimezone(timezone.utc)
                if start <= when <= end:
                    results.append((stop_time, when))
            day += timedelta(days=1)

        results.sort(key=lambda item: item[1])
        return results

    def route_info(self, route_id: str) -> RouteInfo:
        return self.routes.get(route_id) or RouteInfo(route_id, route_id, "")

    def trip_info(self, trip_id: str) -> TripInfo | None:
        return self.trips.get(trip_id)

    def all_routes(self) -> list[RouteInfo]:
        """Toutes les lignes du réseau, triées."""
        return sorted(self.routes.values(), key=lambda r: _line_sort_key(r.short_name))

    def stop_name(self, stop_id: str) -> str:
        stop = self.stops.get(stop_id)
        return stop.name if stop else stop_id


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _parse_gtfs_date(value: str | None) -> date | None:
    """« 20261126 » → date(2026, 11, 26)."""
    value = (value or "").strip()
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        return None


def _parse_gtfs_time(value: str | None) -> int | None:
    """« 25:10:30 » → secondes depuis le début du jour de service."""
    parts = (value or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _normalize(text: str) -> str:
    """Minuscule et sans accents, pour une recherche tolérante."""
    import unicodedata

    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _line_sort_key(short_name: str) -> tuple[int, str]:
    """Trie les lignes numériquement quand c'est possible (1, 2, ..., 10, 21, A, N20)."""
    digits = "".join(c for c in short_name if c.isdigit())
    if digits and digits == short_name:
        return (0, f"{int(digits):04d}")
    if digits:
        return (1, f"{int(digits):04d}{short_name}")
    return (2, short_name)


async def async_get_static_data(
    hass: HomeAssistant, *, force_refresh: bool = False
) -> GtfsStaticData:
    """Retourne les données GTFS statiques, partagées entre le config flow

    et les entrées déjà configurées, pour éviter les téléchargements
    répétés (le fichier fait ~15 Mo).
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    cached: GtfsStaticData | None = domain_data.get(_HASS_DATA_KEY)

    if cached is not None and not force_refresh:
        if time.time() - cached.fetched_at < STATIC_DATA_MAX_AGE.total_seconds():
            return cached

    # Un verrou partagé évite que plusieurs arrêts configurés ne déclenchent
    # chacun leur propre téléchargement/analyse du zip (~15 Mo) en parallèle
    # au démarrage de Home Assistant ou quand le cache expire.
    lock: asyncio.Lock = domain_data.setdefault(_LOCK_KEY, asyncio.Lock())
    async with lock:
        # Une autre entrée a peut-être déjà rafraîchi les données pendant
        # qu'on attendait le verrou : on revérifie avant de télécharger.
        cached = domain_data.get(_HASS_DATA_KEY)
        if cached is not None and not force_refresh:
            if time.time() - cached.fetched_at < STATIC_DATA_MAX_AGE.total_seconds():
                return cached

        session = async_get_clientsession(hass)
        cache_path = hass.config.path(_CACHE_DIRNAME, _CACHE_FILENAME)
        legacy_path = hass.config.path(".storage", _LEGACY_CACHE_FILENAME)
        await hass.async_add_executor_job(_remove_legacy_cache, legacy_path)
        data = await GtfsStaticData.async_load(session, cache_path, force_refresh=force_refresh)
        domain_data[_HASS_DATA_KEY] = data
        return data


def _remove_legacy_cache(legacy_path: str) -> None:
    """Supprime l'ancien cache placé dans .storage/ (versions <= 1.2.0)."""
    for path in (legacy_path, f"{legacy_path}.tmp"):
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as err:
            _LOGGER.debug("Suppression de l'ancien cache %s impossible : %s", path, err)
        else:
            _LOGGER.debug("Ancien cache GTFS supprimé : %s", path)
