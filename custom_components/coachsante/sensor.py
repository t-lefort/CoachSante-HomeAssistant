"""Capteurs CoachSanté : métriques Apple Santé et compteurs nutritionnels."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTEXT_RETENTION_DAYS,
    DEFAULT_CONTEXT_RETENTION_DAYS,
    DOMAIN,
    signal_context_updated,
    signal_metrics_updated,
    signal_nutrition_updated,
    signal_photo_updated,
)
from .data import CoachSanteConfigEntry, CoachSanteData
from .metrics import (
    METRIC_DESCRIPTIONS,
    NUTRITION_DESCRIPTIONS,
    describe_unknown_metric,
)

# Un état Home Assistant ne peut pas dépasser 255 caractères.
MAX_STATE_LENGTH = 255

# Longueur de l'aperçu d'un élément de contexte dans les attributs d'entité.
EXCERPT_LENGTH = 90


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoachSanteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les capteurs d'une personne."""
    data = entry.runtime_data

    entities: list[SensorEntity] = [
        CoachSanteNutritionSensor(data, description)
        for description in NUTRITION_DESCRIPTIONS.values()
    ]
    entities.append(CoachSanteLastMealSensor(data))
    entities.append(CoachSanteMealNoteSensor(data))
    entities.append(CoachSanteContextSensor(data))
    entities.extend(CoachSanteMetricSensor(data, key) for key in data.metrics)
    async_add_entities(entities)

    @callback
    def _add_new_metrics(keys: list[str]) -> None:
        """Crée à la volée les entités des métriques jamais vues jusqu'ici."""
        async_add_entities(CoachSanteMetricSensor(data, key) for key in keys)

    data.async_add_metric_entities = _add_new_metrics

    @callback
    def _forget_callback() -> None:
        data.async_add_metric_entities = None

    entry.async_on_unload(_forget_callback)


class CoachSanteSensorBase(SensorEntity):
    """Base commune : rattachement au device de la personne."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: CoachSanteData) -> None:
        """Rattache l'entité au device représentant la personne."""
        self._data = data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.entry.entry_id)},
            name=data.person,
            manufacturer="CoachSanté",
            model="Apple Santé",
        )


class CoachSanteMetricSensor(CoachSanteSensorBase):
    """Une métrique santé remontée par l'app iOS."""

    def __init__(self, data: CoachSanteData, key: str) -> None:
        """Décrit la métrique à partir du catalogue, ou à défaut à la volée."""
        super().__init__(data)
        self._key = key

        description = METRIC_DESCRIPTIONS.get(key)
        if description is None:
            stored = data.metrics.get(key)
            description = describe_unknown_metric(key, stored.unit if stored else None)
        self.entity_description = description

        self._attr_unique_id = f"{data.entry.entry_id}_metric_{key}"

    @property
    def available(self) -> bool:
        """Indisponible tant que l'app n'a rien envoyé pour cette métrique."""
        return self._key in self._data.metrics

    @property
    def native_value(self) -> Any:
        """Dernière valeur reçue."""
        stored = self._data.metrics.get(self._key)
        return stored.value if stored else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Contexte de la mesure : jour concerné, appareil d'origine."""
        stored = self._data.metrics.get(self._key)
        if stored is None:
            return None
        return {
            "jour": stored.day,
            "source": stored.source,
            "mis_a_jour": stored.updated_at,
        }

    async def async_added_to_hass(self) -> None:
        """S'abonne aux mises à jour de métriques."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_metrics_updated(self._data.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class CoachSanteNutritionSensor(CoachSanteSensorBase):
    """Compteur nutritionnel cumulé sur la journée, remis à zéro à minuit."""

    def __init__(self, data: CoachSanteData, description: SensorEntityDescription) -> None:
        """Initialise le compteur pour un nutriment donné."""
        super().__init__(data)
        self.entity_description = description
        self._attr_unique_id = f"{data.entry.entry_id}_nutrition_{description.key}"

    @property
    def native_value(self) -> float:
        """Total du jour pour ce nutriment."""
        return self._data.nutrition.get(self.entity_description.key, 0.0)

    @property
    def last_reset(self) -> datetime:
        """Minuit du jour en cours, heure locale."""
        return dt_util.start_of_local_day(date.fromisoformat(self._data.nutrition_day))

    async def async_added_to_hass(self) -> None:
        """S'abonne aux mises à jour nutritionnelles."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_nutrition_updated(self._data.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class CoachSanteMealNoteSensor(CoachSanteSensorBase):
    """Commentaire joint à la dernière photo de repas.

    L'événement `coachsante_meal_photo` la porte déjà, mais une automatisation
    n'est pas un endroit où *relire* quelque chose : cette entité rend la note
    visible dans l'interface et la conserve dans l'historique.
    """

    _attr_name = "Note du dernier repas"
    _attr_icon = "mdi:note-text-outline"

    def __init__(self, data: CoachSanteData) -> None:
        """Initialise le capteur de note de repas."""
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_meal_note"

    @property
    def native_value(self) -> str | None:
        """La note, tronquée à la limite d'un état Home Assistant."""
        note = self._data.photo_note
        return note[:MAX_STATE_LENGTH] if note else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """De quelle photo cette note provient."""
        updated = self._data.photo_updated
        return {
            "pris_le": updated.isoformat() if updated else None,
            "chemin": self._data.photo_path,
            "media_content_id": self._data.media_content_id(self._data.photo_path),
        }

    async def async_added_to_hass(self) -> None:
        """S'abonne à l'arrivée des photos de repas."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_photo_updated(self._data.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class CoachSanteContextSensor(CoachSanteSensorBase):
    """Contexte roulant fourni au modèle qui estime les repas.

    L'état compte les éléments encore valides ; l'attribut `prompt` est le bloc
    de texte à injecter tel quel dans l'analyse d'une photo de repas.
    """

    _attr_name = "Contexte nutrition"
    _attr_icon = "mdi:text-box-search-outline"

    def __init__(self, data: CoachSanteData) -> None:
        """Initialise le capteur de contexte."""
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_context"

    @property
    def native_value(self) -> int:
        """Nombre d'éléments de contexte conservés."""
        return len(self._data.context)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Le prompt assemblé, et de quoi inspecter ou supprimer chaque élément.

        Les textes complets ne sont pas repris élément par élément : ils sont déjà
        dans `prompt`, et un attribut d'entité trop gros encombre le recorder.
        """
        return {
            "prompt": self._data.context_prompt,
            "en_attente_analyse": self._data.context_pending_count,
            "retention_jours": self._data.entry.options.get(
                CONF_CONTEXT_RETENTION_DAYS, DEFAULT_CONTEXT_RETENTION_DAYS
            ),
            "elements": [
                {
                    "id": item.id,
                    "le": item.at,
                    "libelle": item.label,
                    "extrait": _excerpt(item.text or item.analysis),
                    "photo": item.photo_path,
                    "en_attente": item.pending_analysis,
                }
                for item in self._data.context
            ],
        }

    async def async_added_to_hass(self) -> None:
        """S'abonne aux mises à jour du contexte."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_context_updated(self._data.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class CoachSanteLastMealSensor(CoachSanteSensorBase):
    """Description du dernier repas enregistré, avec ses macros en attributs."""

    _attr_name = "Dernier repas"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, data: CoachSanteData) -> None:
        """Initialise le capteur de dernier repas."""
        super().__init__(data)
        self._attr_unique_id = f"{data.entry.entry_id}_last_meal"

    @property
    def native_value(self) -> str | None:
        """Libellé du dernier repas, tronqué à la limite d'un état HA."""
        meal = self._data.last_meal
        if meal is None:
            return None
        label = meal.get("label") or "Repas sans libellé"
        return label[:MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Heure et macros du dernier repas, plus le nombre de repas du jour."""
        meal = self._data.last_meal
        if meal is None:
            return None
        attributes = {key: value for key, value in meal.items() if key != "label"}
        attributes["repas_du_jour"] = len(self._data.meals)
        return attributes

    async def async_added_to_hass(self) -> None:
        """S'abonne aux mises à jour nutritionnelles."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_nutrition_updated(self._data.entry.entry_id),
                self.async_write_ha_state,
            )
        )


def _excerpt(text: str | None) -> str | None:
    """Début d'un texte de contexte, pour l'inspection depuis l'interface."""
    if not text:
        return None
    return text if len(text) <= EXCERPT_LENGTH else f"{text[:EXCERPT_LENGTH]}…"
