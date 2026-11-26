"""Tests d'installation complète : capteurs, carte des bus, diagnostics."""
from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.palmbus.diagnostics import async_get_config_entry_diagnostics
from custom_components.palmbus.const import (
    DOMAIN,
    GTFS_RT_ALERTS_URL,
    GTFS_RT_TRIP_UPDATES_URL,
    GTFS_RT_VEHICLE_POSITIONS_URL,
    STATIC_GTFS_URL,
)

from .conftest import NOW, build_gtfs_zip, empty_feed, vehicle_feed

LINES = [{"route_id": "R2", "short_name": "2"}, {"route_id": "R21", "short_name": "21"}]


@pytest.fixture
def mock_feeds(aioclient_mock, trip_feed):
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())
    aioclient_mock.get(
        GTFS_RT_TRIP_UPDATES_URL,
        content=trip_feed(
            {"trip_id": "T1", "updates": [{"stop_id": "S1", "seq": 2, "time": "2026-11-25T07:08:00+00:00", "delay": 180}]}
        ),
    )
    aioclient_mock.get(GTFS_RT_ALERTS_URL, content=empty_feed())
    return aioclient_mock


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _stop_entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hôtel de Ville",
        unique_id="S1",
        data={"stop_id": "S1", "stop_name": "Hôtel de Ville", "available_lines": LINES},
        options=options or {"line_filter": None, "max_departures": 5},
    )


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def test_stop_sensors(hass: HomeAssistant, mock_feeds, freezer) -> None:
    freezer.move_to(NOW)
    entry = _stop_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    nxt = hass.states.get(_entity_id(hass, f"{entry.entry_id}_next_departure"))
    assert nxt.state == "2026-11-25T07:08:00+00:00"
    assert nxt.attributes["ligne"] == "2"
    assert nxt.attributes["retard_minutes"] == 3
    assert nxt.attributes["horaire_theorique"] == "2026-11-25T07:05:00+00:00"
    assert nxt.attributes["dans_minutes"] == 8
    passages = nxt.attributes["prochains_passages"]
    assert [p["temps_reel"] for p in passages[:2]] == [True, False]

    minutes = hass.states.get(_entity_id(hass, f"{entry.entry_id}_minutes_until"))
    assert minutes.state == "8"
    assert minutes.attributes["unit_of_measurement"] == "min"

    line_21 = hass.states.get(_entity_id(hass, f"{entry.entry_id}_line_R21"))
    # Course de nuit « 25:10 » du mercredi → jeudi 01:10 heure de Paris.
    assert line_21.state == "2026-11-26T00:10:00+00:00"
    assert "Line 21" in line_21.attributes["friendly_name"]

    alerts = hass.states.get(_entity_id(hass, f"{entry.entry_id}_alerts"))
    assert alerts.state == "0"


async def test_realtime_down_falls_back_to_schedule(
    hass: HomeAssistant, aioclient_mock, freezer
) -> None:
    freezer.move_to(NOW)
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())
    aioclient_mock.get(GTFS_RT_TRIP_UPDATES_URL, status=503)
    aioclient_mock.get(GTFS_RT_ALERTS_URL, status=503)
    entry = _stop_entry()
    await _setup(hass, entry)

    nxt = hass.states.get(_entity_id(hass, f"{entry.entry_id}_next_departure"))
    assert nxt.state == "2026-11-25T07:05:00+00:00"
    assert nxt.attributes["temps_reel"] is False


async def test_line_sensors_follow_options(hass: HomeAssistant, mock_feeds, freezer) -> None:
    freezer.move_to(NOW)
    entry = _stop_entry()
    await _setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_line_R21")

    hass.config_entries.async_update_entry(entry, options={"line_filter": ["R2"], "max_departures": 5})
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_line_R2")
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_line_R21") is None


async def test_diagnostics(hass: HomeAssistant, mock_feeds, freezer) -> None:
    freezer.move_to(NOW)
    entry = _stop_entry()
    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["gtfs_static"]["routes"] == 2
    assert diag["stop"]["stop_id"] == "S1"
    assert diag["stop"]["next_departures"][0]["realtime"] is True


async def test_vehicles_are_purged(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())
    aioclient_mock.get(
        GTFS_RT_VEHICLE_POSITIONS_URL, content=vehicle_feed({"id": "457"}, {"id": "512"})
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bus en temps réel",
        unique_id="palmbus_vehicles",
        data={"mode": "vehicles", "available_lines": LINES},
        options={"line_filter": None},
    )
    await _setup(hass, entry)
    registry = er.async_get(hass)
    uid_512 = f"{entry.entry_id}_vehicle_512"
    entity_512 = registry.async_get_entity_id("device_tracker", DOMAIN, uid_512)
    assert entity_512
    assert hass.states.get(entity_512).attributes["ligne"] == "2"

    # Le bus 512 quitte le flux : indisponible après 5 min, supprimé après 1 h.
    aioclient_mock.clear_requests()
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())
    aioclient_mock.get(GTFS_RT_VEHICLE_POSITIONS_URL, content=vehicle_feed({"id": "457"}))
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_512).state == "unavailable"

    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("device_tracker", DOMAIN, uid_512) is None
    assert registry.async_get_entity_id("device_tracker", DOMAIN, f"{entry.entry_id}_vehicle_457")
