"""Capteurs Palm Bus : prochains passages et perturbations."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PalmBusConfigEntry
from .const import (
    ATTR_ALERTS,
    ATTR_DELAY,
    ATTR_ESTIMATED_TIME,
    ATTR_HEADSIGN,
    ATTR_LINE,
    ATTR_LINES,
    ATTR_NEXT_DEPARTURES,
    ATTR_REALTIME,
    ATTR_ROUTE_COLOR,
    ATTR_STOP_NAME,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import PalmBusCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PalmBusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs pour cette entrée de configuration."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PalmBusNextDepartureSensor(coordinator, entry),
            PalmBusAlertsSensor(coordinator, entry),
        ]
    )


def _device_info(coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.stop_id)},
        name=f"Palm Bus – {coordinator.stop_name}",
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://palmdeplacements.fr/",
    )


class PalmBusNextDepartureSensor(CoordinatorEntity[PalmBusCoordinator], SensorEntity):
    """Affiche l'heure du prochain passage à l'arrêt suivi."""

    _attr_has_entity_name = True
    _attr_name = "Prochain passage"
    _attr_icon = "mdi:bus-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_departure"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def native_value(self) -> datetime | None:
        departures = self.coordinator.data.departures if self.coordinator.data else []
        return departures[0].estimated if departures else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data or not data.departures:
            return {
                ATTR_STOP_NAME: self.coordinator.stop_name,
                ATTR_NEXT_DEPARTURES: [],
            }

        first = data.departures[0]
        return {
            ATTR_STOP_NAME: data.stop_name,
            ATTR_LINE: first.line,
            ATTR_HEADSIGN: first.headsign,
            ATTR_REALTIME: first.realtime,
            ATTR_DELAY: first.delay_minutes,
            ATTR_ROUTE_COLOR: f"#{first.color}" if first.color else None,
            ATTR_NEXT_DEPARTURES: [
                {
                    ATTR_LINE: dep.line,
                    ATTR_HEADSIGN: dep.headsign,
                    ATTR_ESTIMATED_TIME: dep.estimated.isoformat(),
                    ATTR_REALTIME: dep.realtime,
                    ATTR_DELAY: dep.delay_minutes,
                    ATTR_ROUTE_COLOR: f"#{dep.color}" if dep.color else None,
                }
                for dep in data.departures
            ],
        }

    @property
    def available(self) -> bool:
        # Le capteur reste disponible même sans passage imminent : cela
        # signifie simplement qu'aucun bus n'est prévu dans les prochaines
        # heures (ex : service de nuit), pas une panne de l'intégration.
        return self.coordinator.last_update_success


class PalmBusAlertsSensor(CoordinatorEntity[PalmBusCoordinator], SensorEntity):
    """Nombre de perturbations en cours sur les lignes / l'arrêt suivis."""

    _attr_has_entity_name = True
    _attr_name = "Perturbations"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_native_unit_of_measurement = "perturbation(s)"

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_alerts"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return len(data.alerts) if data else 0

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        alerts = data.alerts if data else []
        return {
            ATTR_LINES: sorted({a.line for a in alerts if a.line}),
            ATTR_ALERTS: [
                {
                    ATTR_LINE: alert.line,
                    "titre": alert.header,
                    "description": alert.description,
                }
                for alert in alerts
            ],
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
