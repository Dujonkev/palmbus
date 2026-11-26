"""Diagnostics Palm Bus (Paramètres → Appareils et services → Palm Bus → Télécharger les diagnostics)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from . import PalmBusConfigEntry
from .coordinator import PalmBusCoordinator
from .vehicles import PalmBusVehiclesCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PalmBusConfigEntry
) -> dict[str, Any]:
    """Résumé de la configuration et du dernier état connu (aucune donnée personnelle)."""
    coordinator = entry.runtime_data
    static = coordinator.static_data
    result: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": {k: v for k, v in entry.data.items() if k != "available_lines"},
            "available_lines": len(entry.data.get("available_lines") or []),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval_s": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
        },
        "gtfs_static": {
            "downloaded_at": datetime.fromtimestamp(static.fetched_at, tz=timezone.utc).isoformat()
            if static.fetched_at
            else None,
            "stops": len(static.stops),
            "routes": len(static.routes),
            "trips": len(static.trips),
            "services_calendar": len(static.calendar),
            "services_calendar_dates": len(static.calendar_dates),
        },
    }

    if isinstance(coordinator, PalmBusCoordinator):
        data = coordinator.data
        result["stop"] = {
            "stop_id": coordinator.stop_id,
            "scheduled_stop_times": len(static.cached_stop_times(coordinator.stop_id)),
            "realtime_available": data.realtime_available if data else None,
            "next_departures": [
                {
                    "line": dep.line,
                    "headsign": dep.headsign,
                    "scheduled": dep.scheduled.isoformat() if dep.scheduled else None,
                    "estimated": dep.estimated.isoformat(),
                    "realtime": dep.realtime,
                    "delay_minutes": dep.delay_minutes,
                }
                for dep in (data.all_departures[:10] if data else [])
            ],
            "alerts": len(data.alerts) if data else 0,
        }
    elif isinstance(coordinator, PalmBusVehiclesCoordinator):
        vehicles = coordinator.data or {}
        result["vehicles"] = {
            "count": len(vehicles),
            "lines": sorted({v.line for v in vehicles.values()}),
        }

    return result
