"""Config flow pour Palm Bus."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AVAILABLE_LINES,
    CONF_LINE_FILTER,
    CONF_MAX_DEPARTURES,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DEFAULT_MAX_DEPARTURES,
    DOMAIN,
    MAX_MAX_DEPARTURES,
    MIN_MAX_DEPARTURES,
)
from .gtfs_static import GtfsStaticData, RouteInfo, StopInfo, async_get_static_data

_LOGGER = logging.getLogger(__name__)


class PalmBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Gère la configuration d'un arrêt Palm Bus."""

    VERSION = 1

    def __init__(self) -> None:
        self._static_data: GtfsStaticData | None = None
        self._matches: dict[str, StopInfo] = {}
        self._selected_stop: StopInfo | None = None
        self._available_routes: list[RouteInfo] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "PalmBusOptionsFlow":
        return PalmBusOptionsFlow(config_entry)

    async def _async_ensure_static_data(self) -> str | None:
        """Charge le GTFS statique. Retourne un code d'erreur en cas d'échec."""
        if self._static_data is not None:
            return None
        try:
            self._static_data = await async_get_static_data(self.hass)
        except aiohttp.ClientError:
            return "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Erreur inattendue au chargement du GTFS Palm Bus")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_ensure_static_data()
            if error:
                errors["base"] = error
            else:
                assert self._static_data is not None
                query = user_input["query"]
                matches = self._static_data.search_stops(query)
                if not matches:
                    errors["base"] = "no_stops_found"
                else:
                    self._matches = {stop.stop_id: stop for stop in matches}
                    return await self.async_step_stop()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("query"): str}),
            errors=errors,
            description_placeholders={"example": "Hôtel de Ville"},
        )

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}

        if user_input is not None:
            stop_id = user_input[CONF_STOP_ID]
            self._selected_stop = self._matches[stop_id]

            await self.async_set_unique_id(stop_id)
            self._abort_if_unique_id_configured()

            assert self._static_data is not None
            try:
                self._available_routes = await self._static_data.async_load_routes_for_stop(
                    stop_id
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Impossible de déterminer les lignes desservant l'arrêt %s", stop_id
                )
                self._available_routes = []

            return await self.async_step_lines()

        options = [
            SelectOptionDict(value=stop.stop_id, label=f"{stop.name} ({stop.stop_id})")
            for stop in sorted(self._matches.values(), key=lambda s: s.name)
        ]
        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STOP_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        assert self._selected_stop is not None

        if user_input is not None:
            selected_lines = user_input.get(CONF_LINE_FILTER) or []
            all_line_ids = [route.route_id for route in self._available_routes]
            # Si tout est sélectionné (ou aucune ligne connue), pas de filtre.
            line_filter = (
                None
                if not selected_lines or set(selected_lines) == set(all_line_ids)
                else selected_lines
            )

            return self.async_create_entry(
                title=self._selected_stop.name,
                data={
                    CONF_STOP_ID: self._selected_stop.stop_id,
                    CONF_STOP_NAME: self._selected_stop.name,
                    CONF_AVAILABLE_LINES: [
                        {"route_id": r.route_id, "short_name": r.short_name}
                        for r in self._available_routes
                    ],
                },
                options={
                    CONF_LINE_FILTER: line_filter,
                    CONF_MAX_DEPARTURES: user_input.get(
                        CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES
                    ),
                },
            )

        line_options = [
            SelectOptionDict(value=route.route_id, label=route.short_name)
            for route in self._available_routes
        ]

        schema_dict: dict[Any, Any] = {}
        if line_options:
            schema_dict[vol.Optional(CONF_LINE_FILTER, default=[])] = SelectSelector(
                SelectSelectorConfig(
                    options=line_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        schema_dict[
            vol.Optional(CONF_MAX_DEPARTURES, default=DEFAULT_MAX_DEPARTURES)
        ] = vol.All(
            vol.Coerce(int), vol.Range(min=MIN_MAX_DEPARTURES, max=MAX_MAX_DEPARTURES)
        )

        return self.async_show_form(
            step_id="lines",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"stop_name": self._selected_stop.name},
        )


class PalmBusOptionsFlow(OptionsFlow):
    """Permet d'ajuster le filtre de lignes et le nombre de passages affichés."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        available_lines = self._config_entry.data.get(CONF_AVAILABLE_LINES, [])

        if user_input is not None:
            selected_lines = user_input.get(CONF_LINE_FILTER) or []
            all_line_ids = [line["route_id"] for line in available_lines]
            line_filter = (
                None
                if not selected_lines or set(selected_lines) == set(all_line_ids)
                else selected_lines
            )
            return self.async_create_entry(
                data={
                    CONF_LINE_FILTER: line_filter,
                    CONF_MAX_DEPARTURES: user_input.get(
                        CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES
                    ),
                }
            )

        current_filter = self._config_entry.options.get(CONF_LINE_FILTER) or [
            line["route_id"] for line in available_lines
        ]
        current_max = self._config_entry.options.get(
            CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES
        )

        line_options = [
            SelectOptionDict(value=line["route_id"], label=line["short_name"])
            for line in available_lines
        ]

        schema_dict: dict[Any, Any] = {}
        if line_options:
            schema_dict[
                vol.Optional(CONF_LINE_FILTER, default=current_filter)
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=line_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        schema_dict[vol.Optional(CONF_MAX_DEPARTURES, default=current_max)] = vol.All(
            vol.Coerce(int), vol.Range(min=MIN_MAX_DEPARTURES, max=MAX_MAX_DEPARTURES)
        )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
