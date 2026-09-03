"""Téléchargement et analyse du GTFS statique de Palm Bus.

Le fichier GTFS statique n'est utilisé que pour :
- rechercher un arrêt par son nom (config flow) ;
- lister les lignes qui desservent un arrêt donné (config flow) ;
- retrouver le nom court d'une ligne, sa couleur et la destination
  (trip_headsign) d'une course pour l'affichage.

Les prochains passages eux-mêmes proviennent uniquement du flux GTFS-RT
(temps réel), ce qui évite de devoir recalculer les horaires théoriques à
partir de calendar.txt / calendar_dates.txt (fichier stop_times.txt de ce
réseau : plus d'un million de lignes, inadapté à un recalcul régulier sur
du matériel domestique).
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

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, MAX_SEARCH_RESULTS, STATIC_DATA_MAX_AGE, STATIC_GTFS_URL

_LOGGER = logging.getLogger(__name__)

_CACHE_FILENAME = "palmbus_gtfs_static.zip"
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


@dataclass
class TripInfo:
    """Une course (trip) du réseau."""

    route_id: str
    headsign: str


@dataclass
class GtfsStaticData:
    """Contenu utile du GTFS statique, une fois analysé."""

    stops: dict[str, StopInfo] = field(default_factory=dict)
    routes: dict[str, RouteInfo] = field(default_factory=dict)
    trips: dict[str, TripInfo] = field(default_factory=dict)
    fetched_at: float = 0.0
    _zip_path: str | None = field(default=None, repr=False)
    _trip_ids_cache: dict[str, set[str]] = field(default_factory=dict, repr=False)

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
        """Analyse stops.txt, routes.txt et trips.txt (tables raisonnables)."""
        loop = asyncio.get_running_loop()
        stops, routes, trips = await loop.run_in_executor(
            None, self._parse_light_tables_sync, self._zip_path
        )
        self.stops = stops
        self.routes = routes
        self.trips = trips

    @staticmethod
    def _parse_light_tables_sync(
        zip_path: str,
    ) -> tuple[dict[str, StopInfo], dict[str, RouteInfo], dict[str, TripInfo]]:
        stops: dict[str, StopInfo] = {}
        routes: dict[str, RouteInfo] = {}
        trips: dict[str, TripInfo] = {}

        with zipfile.ZipFile(zip_path) as archive:
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
                    )

            with archive.open("trips.txt") as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    trip_id = row.get("trip_id")
                    if not trip_id:
                        continue
                    trips[trip_id] = TripInfo(
                        route_id=row.get("route_id", ""),
                        headsign=row.get("trip_headsign") or "",
                    )

        return stops, routes, trips

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
        if stop_id not in self._trip_ids_cache:
            loop = asyncio.get_running_loop()
            trip_ids = await loop.run_in_executor(
                None, self._trip_ids_for_stop_sync, self._zip_path, stop_id
            )
            self._trip_ids_cache[stop_id] = trip_ids
        return self.routes_for_stop_id(stop_id)

    @staticmethod
    def _trip_ids_for_stop_sync(zip_path: str, stop_id: str) -> set[str]:
        trip_ids: set[str] = set()
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open("stop_times.txt") as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                header = next(reader)
                try:
                    stop_idx = header.index("stop_id")
                    trip_idx = header.index("trip_id")
                except ValueError:
                    return trip_ids
                for row in reader:
                    if len(row) > stop_idx and row[stop_idx] == stop_id:
                        trip_ids.add(row[trip_idx])
        return trip_ids

    def route_info(self, route_id: str) -> RouteInfo:
        return self.routes.get(route_id) or RouteInfo(route_id, route_id, "")

    def trip_info(self, trip_id: str) -> TripInfo | None:
        return self.trips.get(trip_id)

    def stop_name(self, stop_id: str) -> str:
        stop = self.stops.get(stop_id)
        return stop.name if stop else stop_id


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
        cache_path = hass.config.path(".storage", _CACHE_FILENAME)
        data = await GtfsStaticData.async_load(session, cache_path, force_refresh=force_refresh)
        domain_data[_HASS_DATA_KEY] = data
        return data
