"""Bus Palm Bus en temps réel sur la carte de Home Assistant."""
from __future__ import annotations

import base64
from html import escape
import time
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BEARING,
    ATTR_HEADSIGN,
    ATTR_LAST_REPORT,
    ATTR_LINE,
    ATTR_ROUTE_COLOR,
    ATTR_SPEED,
    ATTR_STATUS,
    ATTR_STOP,
    ATTR_VEHICLE,
    DOMAIN,
    MANUFACTURER,
    VEHICLE_STALE_AFTER,
)
from .vehicles import PalmBusVehiclesCoordinator, Vehicle


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée une entité par bus, au fur et à mesure qu'ils apparaissent dans le flux."""
    coordinator: PalmBusVehiclesCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [vid for vid in (coordinator.data or {}) if vid not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(PalmBusVehicle(coordinator, entry.entry_id, vid) for vid in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _marker(
    line: str,
    color: str | None,
    text_color: str | None,
    bearing: float | None,
    vehicle: str = "",
) -> str:
    """Icône SVG en data URI : bus vu du dessus orienté selon son cap,
    pastille de ligne (couleur officielle) et numéro du véhicule."""
    fill = f"#{color}" if color else "#0a5aa6"
    text = f"#{text_color}" if text_color else "#ffffff"
    label = escape(line[:3])
    veh = escape(vehicle[:4])
    line_size = 20 if len(label) <= 2 else 15
    angle = round((bearing or 0) / 15) * 15 % 360  # pas de 15° : limite les changements d'image
    bus = (
        f'<g transform="rotate({angle} 60 66)">'
        # ombre
        '<rect x="47" y="35" width="28" height="66" rx="7" fill="#000" opacity="0.18"/>'
        # carrosserie
        '<rect x="45" y="32" width="30" height="68" rx="7" fill="#f4f4f2" stroke="#2b2b2b" stroke-width="2.5"/>'
        # pare-brise (avant = haut) et lunette arrière
        '<path d="M48 36 Q60 31 72 36 L72 45 L48 45 Z" fill="#1d1d1f"/>'
        '<rect x="49" y="92" width="22" height="5" rx="2" fill="#1d1d1f"/>'
        # toit : bandeau à la couleur de la ligne + blocs de climatisation
        f'<rect x="51" y="49" width="18" height="38" rx="3" fill="{fill}" opacity="0.85"/>'
        '<rect x="54" y="55" width="12" height="9" rx="2" fill="#d9d9d6" stroke="#9a9a96" stroke-width="1"/>'
        '<rect x="54" y="71" width="12" height="9" rx="2" fill="#d9d9d6" stroke="#9a9a96" stroke-width="1"/>'
        "</g>"
    )
    badge = (
        f'<circle cx="22" cy="20" r="17" fill="{fill}" stroke="#fff" stroke-width="3"/>'
        f'<text x="22" y="20" dy="0.36em" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
        f'font-weight="bold" font-size="{line_size}" fill="{text}">{label}</text>'
    )
    pill = ""
    if veh:
        pill = (
            '<rect x="40" y="9" width="44" height="22" rx="11" fill="#6b6b6b" stroke="#fff" stroke-width="2"/>'
            '<text x="62" y="20" dy="0.36em" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            f'font-weight="bold" font-size="14" fill="#fff">{veh}</text>'
        )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">{bus}{badge}{pill}</svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


class PalmBusVehicle(CoordinatorEntity[PalmBusVehiclesCoordinator], TrackerEntity):
    """Un bus du réseau, positionné sur la carte."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bus"

    def __init__(self, coordinator: PalmBusVehiclesCoordinator, entry_id: str, vehicle_id: str) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{entry_id}_vehicle_{vehicle_id}"
        self._attr_name = f"Bus {vehicle_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "vehicles")},
            name="Palm Bus – Bus en temps réel",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://palmdeplacements.fr/",
        )

    @property
    def _vehicle(self) -> Vehicle | None:
        return (self.coordinator.data or {}).get(self._vehicle_id)

    @property
    def available(self) -> bool:
        vehicle = self._vehicle
        return (
            super().available
            and vehicle is not None
            and time.monotonic() - vehicle.received < VEHICLE_STALE_AFTER.total_seconds()
        )

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        vehicle = self._vehicle
        return vehicle.latitude if vehicle else None

    @property
    def longitude(self) -> float | None:
        vehicle = self._vehicle
        return vehicle.longitude if vehicle else None

    @property
    def location_accuracy(self) -> int:
        return 0

    @property
    def entity_picture(self) -> str | None:
        vehicle = self._vehicle
        if vehicle is None:
            return None
        return _marker(vehicle.line, vehicle.color, vehicle.text_color, vehicle.bearing, vehicle.label)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        vehicle = self._vehicle
        if vehicle is None:
            return {ATTR_VEHICLE: self._vehicle_id}
        return {
            ATTR_VEHICLE: vehicle.label,
            ATTR_LINE: vehicle.line,
            ATTR_HEADSIGN: vehicle.headsign,
            ATTR_STATUS: vehicle.status,
            ATTR_STOP: vehicle.stop_name,
            ATTR_SPEED: vehicle.speed_kmh,
            ATTR_BEARING: vehicle.bearing,
            ATTR_ROUTE_COLOR: f"#{vehicle.color}" if vehicle.color else None,
            ATTR_LAST_REPORT: vehicle.reported_at.isoformat() if vehicle.reported_at else None,
        }
