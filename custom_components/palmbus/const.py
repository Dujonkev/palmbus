"""Constantes pour l'intégration Palm Bus."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "palmbus"
DEFAULT_NAME = "Palm Bus"
MANUFACTURER = "Palm déplacements (CA Cannes Pays de Lérins)"

# --- Sources de données ouvertes (transport.data.gouv.fr) --------------------
# Fiche du jeu de données :
# https://transport.data.gouv.fr/datasets/horaires-theoriques-et-temps-reel-gtfs-gtfs-rt-du-reseau-palmbus-cannes-pays-de-lerins
#
# Lien pérenne data.gouv.fr vers le GTFS statique (toujours la dernière version).
STATIC_GTFS_URL = "https://www.data.gouv.fr/api/1/datasets/r/47bc8088-6c72-43ad-a959-a5bbdd1aa14f"

# Flux GTFS-RT (protobuf), mis à jour en continu, sans clé d'API.
GTFS_RT_TRIP_UPDATES_URL = (
    "https://proxy.transport.data.gouv.fr/resource/palmbus-cannes-gtfs-rt-trip-update"
)
GTFS_RT_VEHICLE_POSITIONS_URL = (
    "https://proxy.transport.data.gouv.fr/resource/palmbus-cannes-gtfs-rt-vehicle-position"
)
GTFS_RT_ALERTS_URL = (
    "https://proxy.transport.data.gouv.fr/resource/palmbus-cannes-gtfs-rt-service-alert"
)

TIMEZONE = "Europe/Paris"

# --- Configuration -----------------------------------------------------------
CONF_STOP_ID = "stop_id"
CONF_STOP_NAME = "stop_name"
CONF_LINE_FILTER = "line_filter"
CONF_MAX_DEPARTURES = "max_departures"
CONF_AVAILABLE_LINES = "available_lines"

DEFAULT_MAX_DEPARTURES = 5
MIN_MAX_DEPARTURES = 1
MAX_MAX_DEPARTURES = 15

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
STATIC_DATA_MAX_AGE = timedelta(hours=24)

# Fenêtre de recherche des prochains passages théoriques (utilisée pour
# ignorer les données GTFS-RT aberrantes, ex : trajets déjà passés).
DEPARTURE_PAST_TOLERANCE = timedelta(minutes=2)

MAX_SEARCH_RESULTS = 25

ATTR_LINE = "ligne"
ATTR_HEADSIGN = "direction"
ATTR_SCHEDULED_TIME = "horaire_theorique"
ATTR_ESTIMATED_TIME = "horaire_estime"
ATTR_DELAY = "retard_minutes"
ATTR_REALTIME = "temps_reel"
ATTR_ROUTE_COLOR = "couleur_ligne"
ATTR_NEXT_DEPARTURES = "prochains_passages"
ATTR_STOP_NAME = "nom_arret"
ATTR_LINES = "lignes"
ATTR_ALERTS = "perturbations"
