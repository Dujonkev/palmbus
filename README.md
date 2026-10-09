# 🌴🚌 Palm Bus – Home Assistant

Intégration Home Assistant non officielle pour le réseau de transport en commun **Palm Bus** (Cannes Pays de Lérins), basée sur les flux **GTFS** et **GTFS-Realtime** publiés en open data.

![Palm Bus](custom_components/palmbus/brand/logo.png)

## ✨ Fonctionnalités

- Configuration entièrement via l'interface Home Assistant (`config_flow`), sans YAML.
- Suivi d'un ou plusieurs arrêts de bus, avec filtrage par ligne.
- Un capteur **prochain passage** par arrêt (`device_class: timestamp`) avec l'horaire du prochain bus, temps réel si disponible.
- Attributs détaillés sur chaque capteur : ligne, direction, horaire théorique, horaire estimé, retard, couleur de la ligne, et la liste des prochains passages à venir.
- Un capteur **perturbations** par arrêt, avec le nombre de perturbations en cours sur les lignes concernées.
- 🗺️ **Bus en temps réel sur la carte** : chaque bus en circulation apparaît sur la carte de Home Assistant, avec une pastille à la couleur de sa ligne, son numéro et une flèche de direction (mise à jour toutes les 15 secondes).
- Rafraîchissement automatique des données en temps réel (toutes les 30 secondes par défaut).
- Données statiques (arrêts, lignes, horaires théoriques) mises en cache et rafraîchies toutes les 24 heures.
- Logo et icônes dédiés, intégrés nativement dans Home Assistant (page Intégrations).

## 📦 Installation

### Via HACS (dépôt personnalisé)

1. Dans HACS, ajoutez ce dépôt comme dépôt personnalisé (type *Intégration*) : `https://github.com/Dujonkev/palmbus`
2. Installez l'intégration **Palm Bus**.
3. Redémarrez Home Assistant.

### Manuelle

1. Copiez le dossier `custom_components/palmbus` dans le dossier `custom_components` de votre configuration Home Assistant.
2. Redémarrez Home Assistant.

## ⚙️ Configuration

1. Allez dans **Paramètres → Appareils et services → Ajouter une intégration**.
2. Recherchez **Palm Bus**.
3. Recherchez et sélectionnez le ou les arrêts à suivre, puis, si besoin, filtrez par ligne.

Chaque arrêt configuré crée deux capteurs :

| Capteur | Description |
|---|---|
| `sensor.<arret>_prochain_passage` | Horaire du prochain bus (timestamp), avec la liste des prochains passages en attribut |
| `sensor.<arret>_perturbations` | Nombre de perturbations en cours sur les lignes de cet arrêt |

### Exemple d'attributs du capteur « prochain passage »

```yaml
nom_arret: Blanchisserie
ligne: "2"
direction: Blanchisserie
horaire_theorique: "2026-09-03T22:05:54+00:00"
horaire_estime: "2026-09-03T22:05:54+00:00"
temps_reel: true
retard_minutes: null
couleur_ligne: "#3fb5e8"
prochains_passages:
  - ligne: "2"
    direction: Blanchisserie
    horaire_estime: "2026-09-03T22:05:54+00:00"
    temps_reel: true
    retard_minutes: null
    couleur_ligne: "#3fb5e8"
  - ligne: "2"
    direction: Les Bastides
    horaire_estime: "2026-09-03T22:20:00+00:00"
    temps_reel: true
    retard_minutes: null
    couleur_ligne: "#3fb5e8"
```

## 🚌 Bus en temps réel sur la carte

1. **Paramètres → Appareils et services → Ajouter une intégration → Palm Bus**.
2. Choisissez **« Bus en temps réel sur la carte »**, puis les lignes à afficher (vide = toutes).

Chaque bus devient une entité `device_tracker.palm_bus_bus_en_temps_reel_bus_<numéro>` visible sur la carte,
avec les attributs suivants :

```yaml
vehicule: "457"
ligne: A
direction: Gare SNCF de Cannes
statut: En route vers
arret: Passero
vitesse_kmh: 25.2
cap: 2
couleur_ligne: "#00e5ff"
derniere_position: "2026-10-09T18:55:19+00:00"
```

Un bus absent du flux depuis plus de 5 minutes devient indisponible et disparaît de la carte.
Avec une carte filtrée (ex. `auto-entities`), utilisez le motif `device_tracker.palm_bus_bus_en_temps_reel_*`.

## 🗺️ Source des données

Cette intégration s'appuie sur les jeux de données GTFS et GTFS-RT publiés en open data pour le réseau Palmbus (Cannes Pays de Lérins) :
https://www.data.gouv.fr/datasets/horaires-theoriques-et-temps-reel-gtfs-gtfs-rt-du-reseau-palmbus-cannes-pays-de-lerins/

## ⚠️ Avertissement

Ce projet est une intégration communautaire non officielle et n'est affilié ni à Palm Bus, ni à la Communauté d'Agglomération Cannes Pays de Lérins, ni à Home Assistant / Open Home Foundation.

## 📄 Licence

Distribué sous licence MIT — voir le fichier [LICENSE](LICENSE).
