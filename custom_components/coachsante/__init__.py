"""Intégration CoachSanté : passerelle entre l'app iOS et Home Assistant."""

from __future__ import annotations

from functools import partial
import logging

from homeassistant.components import webhook as ha_webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    ATTR_CONTEXT_ID,
    ATTR_ENTRY_ID,
    ATTR_LABEL,
    ATTR_TEXT,
    CONF_PERSON,
    DOMAIN,
    EVENT_CONTEXT,
    EVENT_NUTRITION,
    NUTRIENTS,
    SERVICE_ADD_CONTEXT,
    SERVICE_ADD_NUTRITION,
    SERVICE_CLEAR_CONTEXT,
    SERVICE_RESET_DAY,
    signal_context_updated,
    signal_metrics_updated,
    signal_nutrition_updated,
)
from .data import CoachSanteConfigEntry, CoachSanteData
from .webhook import async_handle_webhook

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR]

ADD_NUTRITION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_LABEL): cv.string,
        **{vol.Optional(nutrient): vol.Coerce(float) for nutrient in NUTRIENTS},
    }
)

RESET_DAY_SCHEMA = vol.Schema({vol.Required(ATTR_ENTRY_ID): cv.string})

ADD_CONTEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_LABEL): cv.string,
        vol.Optional(ATTR_CONTEXT_ID): cv.string,
    }
)

CLEAR_CONTEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_CONTEXT_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Enregistre les services, communs à toutes les personnes configurées."""
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_NUTRITION, partial(_async_add_nutrition, hass), ADD_NUTRITION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESET_DAY, partial(_async_reset_day, hass), RESET_DAY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_CONTEXT, partial(_async_add_context, hass), ADD_CONTEXT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_CONTEXT, partial(_async_clear_context, hass), CLEAR_CONTEXT_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CoachSanteConfigEntry) -> bool:
    """Configure une personne : son état, ses entités, son webhook."""
    data = CoachSanteData(hass, entry)
    await data.async_load()
    entry.runtime_data = data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    ha_webhook.async_register(
        hass,
        DOMAIN,
        f"CoachSanté — {data.person}",
        entry.data[CONF_WEBHOOK_ID],
        partial(async_handle_webhook, entry),
        allowed_methods=["POST"],
        local_only=False,
    )
    entry.async_on_unload(partial(ha_webhook.async_unregister, hass, entry.data[CONF_WEBHOOK_ID]))

    async def _handle_midnight(_now) -> None:
        """Passage de jour : nutrition, « somme du jour », contexte périmé."""
        changed = False
        if data.roll_over_day():
            async_dispatcher_send(hass, signal_nutrition_updated(entry.entry_id))
            changed = True
        if data.reset_stale_daily_metrics():
            async_dispatcher_send(hass, signal_metrics_updated(entry.entry_id))
            changed = True
        if await data.async_purge_expired_context():
            async_dispatcher_send(hass, signal_context_updated(entry.entry_id))
            changed = True
        if changed:
            data.async_schedule_save()

    entry.async_on_unload(
        async_track_time_change(hass, _handle_midnight, hour=0, minute=0, second=0)
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CoachSanteConfigEntry) -> bool:
    """Décharge une personne."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CoachSanteConfigEntry) -> None:
    """Nettoie l'état persistant. Les photos sur disque sont volontairement gardées."""
    data = CoachSanteData(hass, entry)
    await data.async_remove_storage()
    _LOGGER.info("Entrée CoachSanté supprimée. Les photos de repas restent dans %s", data.photo_dir)


async def _async_reload_entry(hass: HomeAssistant, entry: CoachSanteConfigEntry) -> None:
    """Recharge l'entrée quand ses options changent."""
    await hass.config_entries.async_reload(entry.entry_id)


# --- Services --------------------------------------------------------------


def _resolve_entry(hass: HomeAssistant, entry_id: str) -> CoachSanteConfigEntry:
    """Retrouve l'entrée CoachSanté visée par un appel de service."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"person": entry.data.get(CONF_PERSON, entry.title)},
        )
    return entry


async def _async_add_nutrition(hass: HomeAssistant, call: ServiceCall) -> None:
    """Ajoute les macros d'un repas — appelé par l'automatisation d'analyse LLM."""
    entry = _resolve_entry(hass, call.data[ATTR_ENTRY_ID])
    data = entry.runtime_data

    values = {
        nutrient: float(call.data[nutrient]) for nutrient in NUTRIENTS if nutrient in call.data
    }
    if not values:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_nutrient_provided"
        )

    data.add_nutrition(values, call.data.get(ATTR_LABEL))
    data.async_schedule_save()
    async_dispatcher_send(hass, signal_nutrition_updated(entry.entry_id))
    hass.bus.async_fire(
        EVENT_NUTRITION,
        {
            "entry_id": entry.entry_id,
            "person": data.person,
            "label": call.data.get(ATTR_LABEL),
            "added": values,
            "totals": dict(data.nutrition),
        },
    )


async def _async_reset_day(hass: HomeAssistant, call: ServiceCall) -> None:
    """Remet à zéro les compteurs nutritionnels du jour."""
    entry = _resolve_entry(hass, call.data[ATTR_ENTRY_ID])
    data = entry.runtime_data

    data.reset_day()
    data.async_schedule_save()
    async_dispatcher_send(hass, signal_nutrition_updated(entry.entry_id))


async def _async_add_context(hass: HomeAssistant, call: ServiceCall) -> None:
    """Ajoute un texte au contexte, ou complète une photo de contexte reçue.

    Avec `context_id`, le texte est l'analyse de la photo correspondante — c'est
    par là que revient l'automatisation déclenchée par `coachsante_context_photo`.
    Sans lui, c'est un élément de contexte à part entière (lien de recette…).
    """
    entry = _resolve_entry(hass, call.data[ATTR_ENTRY_ID])
    data = entry.runtime_data
    label = call.data.get(ATTR_LABEL)

    if context_id := call.data.get(ATTR_CONTEXT_ID):
        item = data.complete_context(context_id, call.data[ATTR_TEXT], label)
        if item is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="context_not_found",
                translation_placeholders={"context_id": context_id},
            )
    else:
        item = data.add_context(text=call.data[ATTR_TEXT], label=label)

    await data.async_purge_expired_context()
    data.async_schedule_save()
    async_dispatcher_send(hass, signal_context_updated(entry.entry_id))
    hass.bus.async_fire(
        EVENT_CONTEXT,
        {
            "entry_id": entry.entry_id,
            "person": data.person,
            "context_id": item.id,
            "label": item.label,
            "text": item.text,
            "analysis": item.analysis,
            "captured_at": item.at,
        },
    )


async def _async_clear_context(hass: HomeAssistant, call: ServiceCall) -> None:
    """Oublie un élément de contexte, ou tout le contexte de la personne."""
    entry = _resolve_entry(hass, call.data[ATTR_ENTRY_ID])
    data = entry.runtime_data

    removed = await data.async_clear_context(call.data.get(ATTR_CONTEXT_ID))
    if removed:
        data.async_schedule_save()
        async_dispatcher_send(hass, signal_context_updated(entry.entry_id))
