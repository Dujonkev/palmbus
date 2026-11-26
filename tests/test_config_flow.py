"""Tests du config flow (ajout d'un arrêt)."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.palmbus.const import DOMAIN, STATIC_GTFS_URL

from .conftest import build_gtfs_zip


async def test_add_stop(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "search"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"query": "hotel de"})
    assert result["step_id"] == "stop"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stop_id": "S1"})
    assert result["step_id"] == "lines"

    with patch("custom_components.palmbus.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"line_filter": ["R2"], "max_departures": 3}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["stop_id"] == "S1"
    assert result["data"]["available_lines"] == [
        {"route_id": "R2", "short_name": "2"},
        {"route_id": "R21", "short_name": "21"},
    ]
    assert result["options"] == {"line_filter": ["R2"], "max_departures": 3}


async def test_no_stop_found(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(STATIC_GTFS_URL, content=build_gtfs_zip())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "search"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"query": "zzz"})
    assert result["errors"] == {"base": "no_stops_found"}
