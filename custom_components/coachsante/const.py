"""Constantes de l'intégration CoachSanté."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "coachsante"

# --- Configuration ---------------------------------------------------------

CONF_PERSON: Final = "person"
CONF_SECRET: Final = "secret"
CONF_PHOTO_RETENTION: Final = "photo_retention"

# 0 = on ne supprime jamais rien.
DEFAULT_PHOTO_RETENTION: Final = 0

# --- Stockage --------------------------------------------------------------

STORAGE_VERSION: Final = 1

# --- Protocole webhook -----------------------------------------------------

HEADER_SIGNATURE: Final = "X-CoachSante-Signature"
SIGNATURE_PREFIX: Final = "sha256="

# Taille maximale d'un corps de requête accepté (photo en base64 incluse).
MAX_PAYLOAD_BYTES: Final = 12 * 1024 * 1024

# Anti-rejeu : un payload dont `sent_at` est plus vieux que ça est refusé. L'app
# re-date `sent_at` (et re-signe) à chaque tentative d'envoi, si bien que seul le
# rejeu d'une requête capturée sur le réseau tombe hors de cette fenêtre.
REPLAY_MAX_AGE_SECONDS: Final = 300

PAYLOAD_TYPE_METRICS: Final = "metrics"
PAYLOAD_TYPE_MEAL_PHOTO: Final = "meal_photo"

# --- Nutrition -------------------------------------------------------------

NUTRIENTS: Final = (
    "energy_kcal",
    "protein_g",
    "carbs_g",
    "fat_g",
    "fiber_g",
    "sugar_g",
)

# --- Événements ------------------------------------------------------------

EVENT_MEAL_PHOTO: Final = f"{DOMAIN}_meal_photo"
EVENT_METRICS: Final = f"{DOMAIN}_metrics"
EVENT_NUTRITION: Final = f"{DOMAIN}_nutrition"

# --- Services --------------------------------------------------------------

SERVICE_ADD_NUTRITION: Final = "add_nutrition"
SERVICE_RESET_DAY: Final = "reset_day"

ATTR_ENTRY_ID: Final = "entry_id"
ATTR_LABEL: Final = "label"

# --- Signaux internes (dispatcher) -----------------------------------------


def signal_metrics_updated(entry_id: str) -> str:
    """Signal émis quand une ou plusieurs métriques santé changent."""
    return f"{DOMAIN}_metrics_{entry_id}"


def signal_nutrition_updated(entry_id: str) -> str:
    """Signal émis quand les compteurs nutritionnels du jour changent."""
    return f"{DOMAIN}_nutrition_{entry_id}"


def signal_photo_updated(entry_id: str) -> str:
    """Signal émis quand une nouvelle photo de repas arrive."""
    return f"{DOMAIN}_photo_{entry_id}"
