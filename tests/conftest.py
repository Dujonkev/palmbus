"""Fixtures communes : un petit réseau GTFS fictif et des flux GTFS-RT construits à la main."""
from __future__ import annotations

from collections.abc import Callable
import io
from pathlib import Path
import zipfile

from google.transit import gtfs_realtime_pb2
import pytest

# Mercredi 25 novembre 2026, 08:00 à Paris (UTC+1).
NOW = "2026-11-25T07:00:00+00:00"

STOPS = """stop_id,stop_name,stop_lat,stop_lon
S0,Gare SNCF,43.5530,7.0200
S1,Hôtel de Ville,43.5510,7.0100
S2,La Bocca,43.5500,6.9800
"""

ROUTES = """route_id,route_short_name,route_long_name,route_color,route_text_color
R2,2,Gare - La Bocca,3FB5E8,FFFFFF
R21,21,Ligne de nuit,E30613,FFFFFF
"""

TRIPS = """route_id,service_id,trip_id,trip_headsign
R2,SEMAINE,T1,La Bocca
R2,SEMAINE,T2,La Bocca
R2,SEMAINE,T3,La Bocca
R21,SEMAINE,TNUIT,Gare SNCF
R2,FERIE,TFERIE,La Bocca
"""

CALENDAR = """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
SEMAINE,1,1,1,1,1,0,0,20260101,20271231
FERIE,0,0,0,0,0,0,0,20260101,20271231
"""

# FERIE ne circule que le 25/11/2026 ; SEMAINE est supprimé le 26/11/2026.
CALENDAR_DATES = """service_id,date,exception_type
FERIE,20261125,1
SEMAINE,20261126,2
"""

STOP_TIMES = """trip_id,arrival_time,departure_time,stop_id,stop_sequence
T1,07:50:00,07:50:00,S0,1
T1,08:05:00,08:05:00,S1,2
T1,08:15:00,08:15:00,S2,3
T2,08:20:00,08:20:00,S0,1
T2,08:30:00,08:30:00,S1,2
T2,08:40:00,08:40:00,S2,3
T3,08:50:00,08:50:00,S0,1
T3,09:00:00,09:00:00,S1,2
T3,09:10:00,09:10:00,S2,3
TNUIT,24:50:00,24:50:00,S2,1
TNUIT,25:10:00,25:10:00,S1,2
TFERIE,10:00:00,10:00:00,S1,1
"""


def build_gtfs_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("stops.txt", STOPS)
        archive.writestr("routes.txt", ROUTES)
        archive.writestr("trips.txt", TRIPS)
        archive.writestr("calendar.txt", CALENDAR)
        archive.writestr("calendar_dates.txt", CALENDAR_DATES)
        archive.writestr("stop_times.txt", STOP_TIMES)
    return buffer.getvalue()


@pytest.fixture
def gtfs_zip_path(tmp_path: Path) -> str:
    path = tmp_path / "gtfs_static.zip"
    path.write_bytes(build_gtfs_zip())
    return str(path)


def ts(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp())


@pytest.fixture
def trip_feed() -> Callable[..., bytes]:
    """Construit un flux TripUpdates.

    Chaque course : dict(trip_id, route_id="", canceled=False,
    updates=[dict(stop_id, seq, time=iso|None, delay=s|None, skipped=False)]).
    """

    def _build(*trips: dict) -> bytes:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        for index, trip in enumerate(trips):
            entity = feed.entity.add()
            entity.id = f"e{index}"
            update = entity.trip_update
            update.trip.trip_id = trip["trip_id"]
            if trip.get("route_id"):
                update.trip.route_id = trip["route_id"]
            if trip.get("canceled"):
                update.trip.schedule_relationship = update.trip.CANCELED
            for stu_def in trip.get("updates", []):
                stu = update.stop_time_update.add()
                stu.stop_id = stu_def["stop_id"]
                if stu_def.get("seq") is not None:
                    stu.stop_sequence = stu_def["seq"]
                if stu_def.get("skipped"):
                    stu.schedule_relationship = stu.SKIPPED
                    continue
                if stu_def.get("time"):
                    stu.departure.time = ts(stu_def["time"])
                if stu_def.get("delay") is not None:
                    stu.departure.delay = stu_def["delay"]
        return feed.SerializeToString()

    return _build


def vehicle_feed(*vehicles: dict) -> bytes:
    """Flux VehiclePositions : dict(id, trip_id, lat, lon)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for index, veh in enumerate(vehicles):
        entity = feed.entity.add()
        entity.id = f"v{index}"
        pos = entity.vehicle
        pos.vehicle.id = veh["id"]
        pos.vehicle.label = veh["id"]
        pos.trip.trip_id = veh.get("trip_id", "T1")
        pos.position.latitude = veh.get("lat", 43.55)
        pos.position.longitude = veh.get("lon", 7.01)
    return feed.SerializeToString()


def empty_feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    return feed.SerializeToString()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Active le chargement de custom_components/ dans les tests."""
