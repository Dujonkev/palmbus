"""Capteurs Palm Bus : prochains passages, minutes restantes, lignes et perturbations."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
    ATTR_SCHEDULED_TIME,
    ATTR_STOP_NAME,
    CONF_AVAILABLE_LINES,
    CONF_LINE_FILTER,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import Departure, PalmBusCoordinator

ATTR_MINUTES = "dans_minutes"
# Nombre de passages listés dans les attributs d'un capteur de ligne.
LINE_DEPARTURES_IN_ATTRIBUTES = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PalmBusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs pour cette entrée de configuration."""
    coordinator: PalmBusCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        PalmBusNextDepartureSensor(coordinator, entry),
        PalmBusMinutesSensor(coordinator, entry),
        PalmBusAlertsSensor(coordinator, entry),
    ]
    lines = _tracked_lines(entry)
    entities.extend(
        PalmBusLineSensor(coordinator, entry, route_id, short_name)
        for route_id, short_name in lines
    )

    # Capteurs de lignes qui ne sont plus suivies (options modifiées).
    registry = er.async_get(hass)
    wanted = {f"{entry.entry_id}_line_{route_id}" for route_id, _ in lines}
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "sensor"
            and reg_entry.unique_id.startswith(f"{entry.entry_id}_line_")
            and reg_entry.unique_id not in wanted
        ):
            registry.async_remove(reg_entry.entity_id)

    async_add_entities(entities)


def _tracked_lines(entry: PalmBusConfigEntry) -> list[tuple[str, str]]:
    """Lignes suivies à l'arrêt : (route_id, nom court)."""
    available = entry.data.get(CONF_AVAILABLE_LINES) or []
    names = {line["route_id"]: line.get("short_name") or line["route_id"] for line in available}
    selected = entry.options.get(CONF_LINE_FILTER)
    route_ids = selected if selected else list(names)
    return [(route_id, names.get(route_id, route_id)) for route_id in route_ids]


def _device_info(coordinator: PalmBusCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.stop_id)},
        name=f"Palm Bus – {coordinator.stop_name}",
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://palmdeplacements.fr/",
    )


def _departure_attrs(dep: Departure, *, with_line: bool = True) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if with_line:
        attrs[ATTR_LINE] = dep.line
    attrs.update(
        {
            ATTR_HEADSIGN: dep.headsign,
            ATTR_SCHEDULED_TIME: dep.scheduled.isoformat() if dep.scheduled else None,
            ATTR_ESTIMATED_TIME: dep.estimated.isoformat(),
            ATTR_REALTIME: dep.realtime,
            ATTR_DELAY: dep.delay_minutes,
            ATTR_ROUTE_COLOR: f"#{dep.color}" if dep.color else None,
        }
    )
    return attrs


def _minutes_until(when: datetime) -> int:
    seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0, math.floor(seconds / 60))


class _PalmBusSensor(CoordinatorEntity[PalmBusCoordinator], SensorEntity):
    """Base commune : appareil « arrêt » et disponibilité."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(coordinator)

    @property
    def available(self) -> bool:
        # Le capteur reste disponible même sans passage imminent : cela
        # signifie simplement qu'aucun bus n'est prévu (ex : service de nuit),
        # pas une panne de l'intégration.
        return self.coordinator.last_update_success

    @property
    def _departures(self) -> list[Departure]:
        data = self.coordinator.data
        return data.departures if data else []


class PalmBusNextDepartureSensor(_PalmBusSensor):
    """Affiche l'heure du prochain passage à l'arrêt suivi."""

    _attr_translation_key = "next_departure"
    _attr_icon = "mdi:bus-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> None:
        super().__init__(coordinator, entry, "next_departure")

    @property
    def native_value(self) -> datetime | None:
        departures = self._departures
        return departures[0].estimated if departures else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data or not data.departures:
            return {
                ATTR_STOP_NAME: self.coordinator.stop_name,
                ATTR_NEXT_DEPARTURES: [],
            }
        return {
            ATTR_STOP_NAME: data.stop_name,
            **_departure_attrs(data.departures[0]),
            ATTR_MINUTES: _minutes_until(data.departures[0].estimated),
            ATTR_NEXT_DEPARTURES: [
                {**_departure_attrs(dep), ATTR_MINUTES: _minutes_until(dep.estimated)}
                for dep in data.departures
            ],
        }


class PalmBusMinutesSensor(_PalmBusSensor):
    """Nombre de minutes avant le prochain bus (pratique pour les automatisations)."""

    _attr_translation_key = "minutes_until"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> None:
        super().__init__(coordinator, entry, "minutes_until")

    @property
    def native_value(self) -> int | None:
        departures = self._departures
        return _minutes_until(departures[0].estimated) if departures else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        departures = self._departures
        if not departures:
            return {}
        first = departures[0]
        return {ATTR_LINE: first.line, ATTR_HEADSIGN: first.headsign, ATTR_REALTIME: first.realtime}


class PalmBusLineSensor(_PalmBusSensor):
    """Prochain passage d'une ligne donnée à l'arrêt suivi."""

    _attr_translation_key = "line_next_departure"
    _attr_icon = "mdi:bus-stop"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: PalmBusCoordinator,
        entry: PalmBusConfigEntry,
        route_id: str,
        short_name: str,
    ) -> None:
        super().__init__(coordinator, entry, f"line_{route_id}")
        self._route_id = route_id
        self._attr_translation_placeholders = {"line": short_name}

    @property
    def _line_departures(self) -> list[Departure]:
        data = self.coordinator.data
        if not data:
            return []
        return [dep for dep in data.all_departures if dep.route_id == self._route_id]

    @property
    def native_value(self) -> datetime | None:
        departures = self._line_departures
        return departures[0].estimated if departures else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        departures = self._line_departures
        if not departures:
            return {ATTR_NEXT_DEPARTURES: []}
        return {
            **_departure_attrs(departures[0]),
            ATTR_MINUTES: _minutes_until(departures[0].estimated),
            ATTR_NEXT_DEPARTURES: [
                {**_departure_attrs(dep, with_line=False), ATTR_MINUTES: _minutes_until(dep.estimated)}
                for dep in departures[:LINE_DEPARTURES_IN_ATTRIBUTES]
            ],
        }


class PalmBusAlertsSensor(_PalmBusSensor):
    """Nombre de perturbations en cours sur les lignes / l'arrêt suivis."""

    _attr_translation_key = "alerts"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_native_unit_of_measurement = "perturbation(s)"

    def __init__(self, coordinator: PalmBusCoordinator, entry: PalmBusConfigEntry) -> None:
        super().__init__(coordinator, entry, "alerts")

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return len(data.alerts) if data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
