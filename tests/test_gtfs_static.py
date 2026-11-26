"""Tests du GTFS statique : calendriers et horaires théoriques."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.palmbus.gtfs_static import GtfsStaticData, _parse_gtfs_time

PARIS = ZoneInfo("Europe/Paris")


async def _load(path: str) -> GtfsStaticData:
    return await GtfsStaticData.async_load(None, path)  # cache récent : pas de téléchargement


async def test_tables_and_calendar(gtfs_zip_path: str) -> None:
    data = await _load(gtfs_zip_path)
    assert set(data.stops) == {"S0", "S1", "S2"}
    assert data.route_info("R2").short_name == "2"
    assert data.trip_info("T1").service_id == "SEMAINE"

    assert data.is_service_active("SEMAINE", date(2026, 11, 25))  # mercredi
    assert not data.is_service_active("SEMAINE", date(2026, 11, 28))  # samedi
    assert not data.is_service_active("SEMAINE", date(2026, 11, 26))  # supprimé
    assert data.is_service_active("FERIE", date(2026, 11, 25))  # ajouté
    assert not data.is_service_active("FERIE", date(2026, 11, 24))
    assert not data.is_service_active("INCONNU", date(2026, 11, 25))


async def test_routes_for_stop(gtfs_zip_path: str) -> None:
    data = await _load(gtfs_zip_path)
    routes = await data.async_load_routes_for_stop("S1")
    assert [r.short_name for r in routes] == ["2", "21"]


async def test_scheduled_departures(gtfs_zip_path: str) -> None:
    data = await _load(gtfs_zip_path)
    await data.async_load_stop_times("S1")
    start = datetime(2026, 11, 25, 8, 0, tzinfo=PARIS)
    found = data.scheduled_departures("S1", start, start + timedelta(hours=20))
    labels = [(st.trip_id, when.astimezone(PARIS).strftime("%d %H:%M")) for st, when in found]
    assert labels == [
        ("T1", "25 08:05"),
        ("T2", "25 08:30"),
        ("T3", "25 09:00"),
        ("TFERIE", "25 10:00"),
        # Course de nuit « 25:10 » du mercredi : jeudi 01:10.
        ("TNUIT", "26 01:10"),
    ]


async def test_after_midnight_belongs_to_previous_service_day(gtfs_zip_path: str) -> None:
    """Jeudi 26 (SEMAINE supprimé) à 00:30 : la course de nuit de mercredi passe encore."""
    data = await _load(gtfs_zip_path)
    await data.async_load_stop_times("S1")
    start = datetime(2026, 11, 26, 0, 30, tzinfo=PARIS)
    found = data.scheduled_departures("S1", start, start + timedelta(hours=12))
    assert [st.trip_id for st, _ in found] == ["TNUIT"]


async def test_dst_change_uses_noon_minus_12h(gtfs_zip_path: str) -> None:
    """Le jour du passage à l'heure d'été, 08:05 reste 08:05 heure locale."""
    data = await _load(gtfs_zip_path)
    await data.async_load_stop_times("S1")
    start = datetime(2026, 3, 30, 0, 0, tzinfo=PARIS)  # lundi après le changement d'heure
    found = dict(
        (st.trip_id, when) for st, when in data.scheduled_departures("S1", start, start + timedelta(hours=10))
    )
    assert found["T1"].astimezone(PARIS).strftime("%H:%M") == "08:05"


def test_parse_gtfs_time() -> None:
    assert _parse_gtfs_time("25:10:30") == 25 * 3600 + 10 * 60 + 30
    assert _parse_gtfs_time("") is None
    assert _parse_gtfs_time("8:00") is None
