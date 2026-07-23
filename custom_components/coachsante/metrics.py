"""Catalogue des métriques santé remontées par l'app iOS.

Chaque clé correspond à un type de données Apple Santé (HealthKit) déjà **agrégé
par l'app** : c'est l'app qui fait tourner les `HKStatisticsQuery`, parce que
HealthKit sait dédupliquer les échantillons qui arrivent en double de l'iPhone et
de l'Apple Watch — un simple envoi d'échantillons bruts compterait les pas deux
fois.

Les clés inconnues de ce catalogue sont quand même acceptées : une entité
générique est créée à la volée. Le catalogue ne sert qu'à donner un joli nom
français, une unité, une icône et les bons `device_class`/`state_class`.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfMass,
    UnitOfSoundPressure,
    UnitOfSpeed,
    UnitOfTime,
    UnitOfVolume,
)

# Unités qui n'existent pas dans les constantes Home Assistant.
UNIT_KCAL = "kcal"
UNIT_BPM = "bpm"
UNIT_MILLISECONDS = "ms"
UNIT_VO2_MAX = "mL/kg/min"
UNIT_BREATHS_PER_MINUTE = "resp/min"
UNIT_STEPS = "pas"
UNIT_FLIGHTS = "étages"
UNIT_WORKOUTS = "séances"
UNIT_GRAMS = UnitOfMass.GRAMS


def _m(
    key: str,
    name: str,
    *,
    unit: str | None = None,
    icon: str | None = None,
    device_class: SensorDeviceClass | None = None,
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT,
    precision: int | None = None,
) -> SensorEntityDescription:
    """Raccourci pour décrire une métrique sans répéter les mots-clés."""
    return SensorEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=unit,
        icon=icon,
        device_class=device_class,
        state_class=state_class,
        suggested_display_precision=precision,
    )


_DAILY = SensorStateClass.TOTAL_INCREASING

_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    # --- Activité ---------------------------------------------------------
    _m("steps", "Pas", unit=UNIT_STEPS, icon="mdi:shoe-print", state_class=_DAILY, precision=0),
    _m(
        "distance_walking_running",
        "Distance marche et course",
        unit=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=_DAILY,
        precision=2,
    ),
    _m(
        "distance_cycling",
        "Distance à vélo",
        unit=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=_DAILY,
        precision=2,
    ),
    _m(
        "distance_swimming",
        "Distance à la nage",
        unit=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "flights_climbed",
        "Étages montés",
        unit=UNIT_FLIGHTS,
        icon="mdi:stairs-up",
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "active_energy",
        "Dépense énergétique active",
        unit=UNIT_KCAL,
        icon="mdi:fire",
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "basal_energy",
        "Dépense énergétique au repos",
        unit=UNIT_KCAL,
        icon="mdi:sleep",
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "exercise_minutes",
        "Minutes d'exercice",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "stand_hours",
        "Heures debout",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "workout_count",
        "Séances du jour",
        unit=UNIT_WORKOUTS,
        icon="mdi:dumbbell",
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "workout_minutes",
        "Durée des séances",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY,
        precision=0,
    ),
    # --- Cœur et respiration ----------------------------------------------
    _m("heart_rate", "Fréquence cardiaque", unit=UNIT_BPM, icon="mdi:heart-pulse", precision=0),
    _m(
        "resting_heart_rate",
        "Fréquence cardiaque au repos",
        unit=UNIT_BPM,
        icon="mdi:heart",
        precision=0,
    ),
    _m(
        "walking_heart_rate",
        "Fréquence cardiaque à la marche",
        unit=UNIT_BPM,
        icon="mdi:heart",
        precision=0,
    ),
    _m(
        "heart_rate_variability",
        "Variabilité cardiaque",
        unit=UNIT_MILLISECONDS,
        icon="mdi:heart-flash",
        precision=0,
    ),
    _m("vo2_max", "VO2 max", unit=UNIT_VO2_MAX, icon="mdi:lungs", precision=1),
    _m("blood_oxygen", "Oxygène sanguin", unit=PERCENTAGE, icon="mdi:water-percent", precision=0),
    _m(
        "respiratory_rate",
        "Fréquence respiratoire",
        unit=UNIT_BREATHS_PER_MINUTE,
        icon="mdi:lungs",
        precision=0,
    ),
    # --- Corps ------------------------------------------------------------
    # Le poids fait autorité côté intégration Withings ; ces entités ne servent
    # qu'aux éventuelles saisies manuelles dans Apple Santé.
    _m(
        "body_mass",
        "Poids (Apple Santé)",
        unit=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        precision=1,
    ),
    _m("body_fat", "Masse grasse", unit=PERCENTAGE, icon="mdi:percent", precision=1),
    _m(
        "lean_body_mass",
        "Masse maigre",
        unit=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        precision=1,
    ),
    _m("bmi", "IMC", icon="mdi:human", precision=1),
    _m(
        "height",
        "Taille",
        unit=UnitOfLength.CENTIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        precision=0,
    ),
    _m(
        "waist_circumference",
        "Tour de taille",
        unit=UnitOfLength.CENTIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        precision=1,
    ),
    # --- Sommeil ----------------------------------------------------------
    _m(
        "sleep_duration",
        "Sommeil",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    _m(
        "sleep_deep",
        "Sommeil profond",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    _m(
        "sleep_rem",
        "Sommeil paradoxal",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    _m(
        "sleep_core",
        "Sommeil léger",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    _m(
        "sleep_awake",
        "Éveil nocturne",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    _m(
        "time_in_bed",
        "Temps passé au lit",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        precision=2,
    ),
    # --- Mobilité ---------------------------------------------------------
    # Utile pour suivre le genou : une asymétrie qui grimpe est un signal.
    _m(
        "walking_speed",
        "Vitesse de marche",
        unit=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        precision=2,
    ),
    _m(
        "walking_step_length",
        "Longueur de pas",
        unit=UnitOfLength.CENTIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        precision=0,
    ),
    _m("walking_asymmetry", "Asymétrie de marche", unit=PERCENTAGE, icon="mdi:walk", precision=1),
    _m("walking_double_support", "Double appui", unit=PERCENTAGE, icon="mdi:walk", precision=1),
    _m(
        "stair_ascent_speed",
        "Vitesse en montée d'escalier",
        unit=UnitOfSpeed.METERS_PER_SECOND,
        device_class=SensorDeviceClass.SPEED,
        precision=2,
    ),
    _m(
        "stair_descent_speed",
        "Vitesse en descente d'escalier",
        unit=UnitOfSpeed.METERS_PER_SECOND,
        device_class=SensorDeviceClass.SPEED,
        precision=2,
    ),
    _m(
        "six_minute_walk_distance",
        "Distance de marche en 6 minutes",
        unit=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        precision=0,
    ),
    # --- Divers -----------------------------------------------------------
    _m(
        "mindful_minutes",
        "Minutes de méditation",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "water",
        "Eau bue",
        unit=UnitOfVolume.LITERS,
        icon="mdi:cup-water",
        state_class=_DAILY,
        precision=2,
    ),
    _m(
        "dietary_energy",
        "Alimentation saisie dans Apple Santé",
        unit=UNIT_KCAL,
        icon="mdi:food-apple",
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "daylight_minutes",
        "Temps à la lumière du jour",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY,
        precision=0,
    ),
    _m(
        "audio_exposure_environment",
        "Exposition sonore ambiante",
        unit=UnitOfSoundPressure.DECIBEL,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        precision=0,
    ),
    _m(
        "audio_exposure_headphones",
        "Exposition sonore au casque",
        unit=UnitOfSoundPressure.DECIBEL,
        device_class=SensorDeviceClass.SOUND_PRESSURE,
        precision=0,
    ),
)

METRIC_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    description.key: description for description in _DESCRIPTIONS
}


# --- Nutrition -------------------------------------------------------------
# Compteurs cumulés sur la journée, remis à zéro à minuit. `TOTAL` + `last_reset`
# est le motif documenté par Home Assistant pour un compteur dont on connaît la
# date de remise à zéro.

NUTRITION_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    description.key: description
    for description in (
        _m(
            "energy_kcal",
            "Calories du jour",
            unit=UNIT_KCAL,
            icon="mdi:fire",
            state_class=SensorStateClass.TOTAL,
            precision=0,
        ),
        _m(
            "protein_g",
            "Protéines du jour",
            unit=UNIT_GRAMS,
            icon="mdi:food-steak",
            state_class=SensorStateClass.TOTAL,
            precision=1,
        ),
        _m(
            "carbs_g",
            "Glucides du jour",
            unit=UNIT_GRAMS,
            icon="mdi:baguette",
            state_class=SensorStateClass.TOTAL,
            precision=1,
        ),
        _m(
            "fat_g",
            "Lipides du jour",
            unit=UNIT_GRAMS,
            icon="mdi:oil",
            state_class=SensorStateClass.TOTAL,
            precision=1,
        ),
        _m(
            "fiber_g",
            "Fibres du jour",
            unit=UNIT_GRAMS,
            icon="mdi:barley",
            state_class=SensorStateClass.TOTAL,
            precision=1,
        ),
        _m(
            "sugar_g",
            "Sucres du jour",
            unit=UNIT_GRAMS,
            icon="mdi:candy",
            state_class=SensorStateClass.TOTAL,
            precision=1,
        ),
    )
}


def describe_unknown_metric(key: str, unit: str | None) -> SensorEntityDescription:
    """Décrit une métrique absente du catalogue.

    L'app iOS peut remonter n'importe quel type HealthKit ; on ne veut pas qu'une
    donnée soit perdue juste parce qu'on ne l'avait pas prévue ici.
    """
    return _m(key, key.replace("_", " ").capitalize(), unit=unit, icon="mdi:heart-pulse")
