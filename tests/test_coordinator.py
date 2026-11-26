"""Tests de la fusion horaires théoriques + temps réel."""
from __future__ import annotations

from google.transit import gtfs_realtime_pb2
import pytest

from homeassistant.core import HomeAssistant

from custom_components.palmbus.coordinator import PalmBusCoordinator
from custom_components.palmbus.gtfs_static import GtfsStaticData

from .conftest import NOW


@pytest.fixture
async def coordinator(hass: HomeAssistant, gtfs_zip_path: str, freezer) -> PalmBusCoordinator:
    freezer.move_to(NOW)
    static = await GtfsStaticData.async_load(None, gtfs_zip_path)
    coord = PalmBusCoordinator(
        hass, static, stop_id="S1", stop_name="Hôtel de Ville", line_filter=None, max_departures=5
    )
    await static.async_load_stop_times("S1")
    return coord


def _parse(raw: bytes) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    return feed


def _summary(departures) -> list[tuple]:
    return [
        (d.trip_id, d.estimated.isoformat()[11:16], d.realtime, d.delay_minutes) for d in departures
    ]


async def test_theoretical_only(coordinator: PalmBusCoordinator) -> None:
    """Sans flux temps réel, les horaires théoriques prennent le relais (UTC dans les résultats)."""
    deps = coordinator._build_departures(None, coordinator.static_data.cached_stop_times("S1"))
    assert _summary(deps)[:4] == [
        ("T1", "07:05", False, None),
        ("T2", "07:30", False, None),
        ("T3", "08:00", False, None),
        ("TFERIE", "09:00", False, None),
    ]
    assert deps[0].scheduled == deps[0].estimated
    assert deps[0].line == "2" and deps[0].headsign == "La Bocca"


async def test_realtime_overrides_schedule(coordinator: PalmBusCoordinator, trip_feed) -> None:
    feed = _parse(
        trip_feed(
            {"trip_id": "T1", "updates": [{"stop_id": "S1", "seq": 2, "time": "2026-11-25T07:09:00+00:00", "delay": 240}]},
            {"trip_id": "T2", "canceled": True},
            {"trip_id": "T3", "updates": [{"stop_id": "S1", "seq": 2, "skipped": True}]},
        )
    )
    deps = coordinator._build_departures(feed, coordinator.static_data.cached_stop_times("S1"))
    summary = _summary(deps)
    assert summary[0] == ("T1", "07:09", True, 4)
    assert deps[0].scheduled.isoformat()[11:16] == "07:05"
    ids = [d.trip_id for d in deps]
    assert "T2" not in ids  # annulée
    assert "T3" not in ids  # arrêt non desservi


async def test_delay_propagated_from_previous_stop(coordinator: PalmBusCoordinator, trip_feed) -> None:
    feed = _parse(trip_feed({"trip_id": "T2", "updates": [{"stop_id": "S0", "seq": 1, "delay": 300}]}))
    deps = coordinator._build_departures(feed, coordinator.static_data.cached_stop_times("S1"))
    t2 = next(d for d in deps if d.trip_id == "T2")
    assert (t2.estimated.isoformat()[11:16], t2.realtime, t2.delay_minutes) == ("07:35", True, 5)


async def test_bus_already_passed(coordinator: PalmBusCoordinator, trip_feed) -> None:
    """Seules des mises à jour après notre arrêt : le bus est passé, on ne l'affiche pas."""
    feed = _parse(trip_feed({"trip_id": "T1", "updates": [{"stop_id": "S2", "seq": 3, "delay": 0}]}))
    deps = coordinator._build_departures(feed, coordinator.static_data.cached_stop_times("S1"))
    assert "T1" not in [d.trip_id for d in deps]


async def test_late_bus_still_expected(coordinator: PalmBusCoordinator, trip_feed, freezer) -> None:
    """Heure théorique dépassée mais bus en retard : toujours attendu."""
    freezer.move_to("2026-11-25T07:20:00+00:00")  # 08:20, T1 prévu à 08:05
    feed = _parse(
        trip_feed({"trip_id": "T1", "updates": [{"stop_id": "S1", "seq": 2, "time": "2026-11-25T07:25:00+00:00"}]})
    )
    deps = coordinator._build_departures(feed, coordinator.static_data.cached_stop_times("S1"))
    assert _summary(deps)[0] == ("T1", "07:25", True, 20)


async def test_realtime_trip_unknown_in_static(coordinator: PalmBusCoordinator, trip_feed) -> None:
    feed = _parse(
        trip_feed(
            {"trip_id": "EXTRA", "route_id": "R21", "updates": [{"stop_id": "S1", "time": "2026-11-25T07:01:00+00:00"}]}
        )
    )
    deps = coordinator._build_departures(feed, coordinator.static_data.cached_stop_times("S1"))
    assert (deps[0].trip_id, deps[0].line, deps[0].realtime, deps[0].scheduled) == ("EXTRA", "21", True, None)


async def test_line_filter(coordinator: PalmBusCoordinator) -> None:
    coordinator.line_filter = {"R21"}
    deps = coordinator._build_departures(None, coordinator.static_data.cached_stop_times("S1"))
    assert {d.line for d in deps} == {"21"}
