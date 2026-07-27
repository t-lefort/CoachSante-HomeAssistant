"""Constantes de l'intégration CoachSanté."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "coachsante"

# --- Configuration ---------------------------------------------------------

CONF_PERSON: Final = "person"
CONF_SECRET: Final = "secret"
CONF_PHOTO_RETENTION: Final = "photo_retention"
CONF_CONTEXT_RETENTION_DAYS: Final = "context_retention_days"

# 0 = on ne supprime jamais rien.
DEFAULT_PHOTO_RETENTION: Final = 0

# Durée de vie d'un élément de contexte (lien de recette, description d'un
# emballage…). Deux semaines : assez pour couvrir les restes et les recettes de
# la semaine, assez court pour que le prompt ne se remplisse pas de vieilleries.
DEFAULT_CONTEXT_RETENTION_DAYS: Final = 14

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
PAYLOAD_TYPE_CONTEXT: Final = "context"
PAYLOAD_TYPE_GOAL: Final = "goal"
# Détail horaire, rangé dans les statistiques long terme (voir `series.py`).
PAYLOAD_TYPE_SERIES: Final = "series"

# --- Contexte nutritionnel -------------------------------------------------
# Bornes volontairement basses : le contenu part en attribut d'entité, et un
# attribut trop gros encombre le recorder (au-delà de ~16 Kio il est refusé).

MAX_CONTEXT_ITEMS: Final = 30
MAX_CONTEXT_TEXT_LENGTH: Final = 2000
MAX_CONTEXT_PROMPT_LENGTH: Final = 6000

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
# Photo de contexte reçue : c'est le signal qui demande son analyse par un LLM.
EVENT_CONTEXT_PHOTO: Final = f"{DOMAIN}_context_photo"
# Un texte de contexte est disponible (saisi dans l'app, ou analyse revenue).
EVENT_CONTEXT: Final = f"{DOMAIN}_context"

# --- Services --------------------------------------------------------------

SERVICE_ADD_NUTRITION: Final = "add_nutrition"
SERVICE_RESET_DAY: Final = "reset_day"
SERVICE_ADD_CONTEXT: Final = "add_context"
SERVICE_CLEAR_CONTEXT: Final = "clear_context"

ATTR_ENTRY_ID: Final = "entry_id"
ATTR_LABEL: Final = "label"
ATTR_TEXT: Final = "text"
ATTR_CONTEXT_ID: Final = "context_id"

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


def signal_context_updated(entry_id: str) -> str:
    """Signal émis quand le contexte nutritionnel change."""
    return f"{DOMAIN}_context_{entry_id}"


def signal_goal_updated(entry_id: str) -> str:
    """Signal émis quand l'objectif personnel change."""
    return f"{DOMAIN}_goal_{entry_id}"
