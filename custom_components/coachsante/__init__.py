"""Intégration CoachSanté : passerelle entre l'app iOS et Home Assistant."""

from __future__ import annotations

from functools import partial
import logging

from homeassistant.components import webhook as ha_webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    ATTR_ENTRY_ID,
    ATTR_LABEL,
    CONF_PERSON,
    DOMAIN,
    EVENT_NUTRITION,
    NUTRIENTS,
    SERVICE_ADD_NUTRITION,
    SERVICE_RESET_DAY,
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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Enregistre les services, communs à toutes les personnes configurées."""
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_NUTRITION, partial(_async_add_nutrition, hass), ADD_NUTRITION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESET_DAY, partial(_async_reset_day, hass), RESET_DAY_SCHEMA
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

    @callback
    def _handle_midnight(_now) -> None:
        """Remet les compteurs nutritionnels à zéro au changement de jour."""
        if data.roll_over_day():
            data.async_schedule_save()
            async_dispatcher_send(hass, signal_nutrition_updated(entry.entry_id))

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
