"""Intégration Palm Bus (réseau de bus de la CA Cannes Pays de Lérins).

Prochains passages en temps réel à un arrêt, à partir des données GTFS /
GTFS-RT ouvertes publiées sur transport.data.gouv.fr. Aucune clé d'API
n'est nécessaire.
"""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_LINE_FILTER, CONF_MAX_DEPARTURES, CONF_STOP_ID, CONF_STOP_NAME, DEFAULT_MAX_DEPARTURES
from .coordinator import PalmBusCoordinator
from .gtfs_static import async_get_static_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

PalmBusConfigEntry = ConfigEntry[PalmBusCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PalmBusConfigEntry) -> bool:
    """Initialise une entrée de configuration Palm Bus."""
    try:
        static_data = await async_get_static_data(hass)
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise ConfigEntryNotReady(
            "Impossible de télécharger les données GTFS statiques de Palm Bus"
        ) from err

    coordinator = PalmBusCoordinator(
        hass,
        static_data,
        stop_id=entry.data[CONF_STOP_ID],
        stop_name=entry.data[CONF_STOP_NAME],
        line_filter=entry.options.get(CONF_LINE_FILTER),
        max_departures=entry.options.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PalmBusConfigEntry) -> bool:
    """Décharge une entrée de configuration Palm Bus."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: PalmBusConfigEntry) -> None:
    """Recharge l'entrée quand les options (lignes suivies, nb de passages) changent."""
    await hass.config_entries.async_reload(entry.entry_id)
