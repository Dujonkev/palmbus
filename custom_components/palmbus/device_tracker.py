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
    uid = f"{label}{angle}".replace(" ", "")
    bus = (
        "<defs>"
        # carrosserie : dégradé latéral (effet de volume)
        f'<linearGradient id="b{uid}" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0" stop-color="#b9bcc0"/><stop offset="0.18" stop-color="#ffffff"/>'
        '<stop offset="0.82" stop-color="#f1f2f3"/><stop offset="1" stop-color="#9da1a6"/></linearGradient>'
        # vitres : reflet
        f'<linearGradient id="g{uid}" x1="0" x2="1" y1="0" y2="1">'
        '<stop offset="0" stop-color="#5b6b7c"/><stop offset="0.45" stop-color="#1b232c"/>'
        '<stop offset="1" stop-color="#0b0f14"/></linearGradient>'
        # toit à la couleur de la ligne, plus clair au centre
        f'<linearGradient id="r{uid}" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{fill}" stop-opacity="0.75"/><stop offset="0.5" stop-color="{fill}"/>'
        f'<stop offset="1" stop-color="{fill}" stop-opacity="0.75"/></linearGradient>'
        f'<filter id="s{uid}" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="2.2"/></filter>'
        "</defs>"
        f'<g transform="rotate({angle} 60 68)">'
        # ombre portée au sol
        f'<rect x="47" y="35" width="30" height="70" rx="8" fill="#000" opacity="0.45" filter="url(#s{uid})"/>'
        # roues (dépassent légèrement)
        '<rect x="42.5" y="44" width="4" height="10" rx="2" fill="#111"/>'
        '<rect x="73.5" y="44" width="4" height="10" rx="2" fill="#111"/>'
        '<rect x="42.5" y="84" width="4" height="10" rx="2" fill="#111"/>'
        '<rect x="73.5" y="84" width="4" height="10" rx="2" fill="#111"/>'
        # carrosserie
        f'<rect x="44" y="31" width="32" height="70" rx="8" fill="url(#b{uid})" stroke="#3a3d41" stroke-width="1.6"/>'
        # pare-brise avant (haut) et lunette arrière
        f'<path d="M47 37 Q60 30.5 73 37 L72 46 Q60 43 48 46 Z" fill="url(#g{uid})"/>'
        '<path d="M50 37.5 Q55 35 60 34.5 L58 40 Q53 40.5 49.5 42 Z" fill="#ffffff" opacity="0.35"/>'
        f'<rect x="49" y="94" width="22" height="4.5" rx="2" fill="url(#g{uid})"/>'
        # vitres latérales
        f'<rect x="45.5" y="49" width="2.6" height="42" rx="1.2" fill="url(#g{uid})"/>'
        f'<rect x="71.9" y="49" width="2.6" height="42" rx="1.2" fill="url(#g{uid})"/>'
        # toit coloré + équipements avec ombre/éclairage
        f'<rect x="50" y="48" width="20" height="44" rx="4" fill="url(#r{uid})"/>'
        '<rect x="53" y="54" width="14" height="10" rx="2.5" fill="#000" opacity="0.25" transform="translate(1.2 1.2)"/>'
        '<rect x="53" y="54" width="14" height="10" rx="2.5" fill="#eceeef" stroke="#8d9196" stroke-width="0.8"/>'
        '<rect x="53" y="72" width="14" height="10" rx="2.5" fill="#000" opacity="0.25" transform="translate(1.2 1.2)"/>'
        '<rect x="53" y="72" width="14" height="10" rx="2.5" fill="#eceeef" stroke="#8d9196" stroke-width="0.8"/>'
        '<line x1="55" y1="59" x2="65" y2="59" stroke="#b5b9bd" stroke-width="1"/>'
        '<line x1="55" y1="77" x2="65" y2="77" stroke="#b5b9bd" stroke-width="1"/>'
        # reflet de lumière sur le toit
        '<rect x="52" y="49.5" width="5" height="40" rx="2.5" fill="#fff" opacity="0.25"/>'
        # phares
        '<circle cx="49" cy="33.5" r="1.4" fill="#fff8c4"/><circle cx="71" cy="33.5" r="1.4" fill="#fff8c4"/>'
        "</g>"
    )
    badge = (
        '<circle cx="23" cy="21.5" r="17" fill="#000" opacity="0.3"/>'
        f'<circle cx="22" cy="20" r="17" fill="{fill}" stroke="#fff" stroke-width="3"/>'
        '<ellipse cx="22" cy="12" rx="11" ry="5" fill="#fff" opacity="0.28"/>'
        f'<text x="22" y="20" dy="0.36em" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
        f'font-weight="bold" font-size="{line_size}" fill="{text}">{label}</text>'
    )
    pill = ""
    if veh:
        pill = (
            '<rect x="41" y="10.5" width="44" height="22" rx="11" fill="#000" opacity="0.3"/>'
            '<rect x="40" y="9" width="44" height="22" rx="11" fill="#6b6b6b" stroke="#fff" stroke-width="2"/>'
            '<rect x="45" y="11" width="34" height="6" rx="3" fill="#fff" opacity="0.18"/>'
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
