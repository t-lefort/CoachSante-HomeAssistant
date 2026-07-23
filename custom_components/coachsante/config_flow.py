"""Config flow : une entrée par personne suivie."""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.components import webhook as ha_webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import slugify
import voluptuous as vol

from .const import (
    CONF_PERSON,
    CONF_PHOTO_RETENTION,
    CONF_SECRET,
    DEFAULT_PHOTO_RETENTION,
    DOMAIN,
)
from .data import CoachSanteConfigEntry

USER_SCHEMA = vol.Schema({vol.Required(CONF_PERSON): cv.string})


class CoachSanteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Demande le prénom, puis affiche l'URL et le secret à recopier dans l'app."""

    VERSION = 1

    def __init__(self) -> None:
        """Prépare les identifiants générés entre les deux étapes."""
        self._person: str = ""
        self._webhook_id: str = ""
        self._secret: str = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Demande de qui il s'agit."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)

        person = user_input[CONF_PERSON].strip()
        if not person:
            return self.async_show_form(
                step_id="user", data_schema=USER_SCHEMA, errors={CONF_PERSON: "person_empty"}
            )

        await self.async_set_unique_id(slugify(person))
        self._abort_if_unique_id_configured()

        self._person = person
        self._webhook_id = ha_webhook.async_generate_id()
        self._secret = secrets.token_hex(32)

        return await self.async_step_credentials()

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Seconde étape : afficher les identifiants une bonne fois pour toutes."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._person,
                data={
                    CONF_PERSON: self._person,
                    CONF_WEBHOOK_ID: self._webhook_id,
                    CONF_SECRET: self._secret,
                },
            )

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema({}),
            description_placeholders={
                "person": self._person,
                "url": self._webhook_url(),
                "secret": self._secret,
            },
        )

    def _webhook_url(self) -> str:
        """URL complète du webhook, ou juste le chemin si aucune URL externe n'est connue."""
        try:
            return ha_webhook.async_generate_url(self.hass, self._webhook_id)
        except NoURLAvailableError:
            return ha_webhook.async_generate_path(self._webhook_id)

    @staticmethod
    def async_get_options_flow(entry: CoachSanteConfigEntry) -> OptionsFlow:
        """Expose les options modifiables après coup."""
        return CoachSanteOptionsFlow()


class CoachSanteOptionsFlow(OptionsFlow):
    """Options : rétention des photos de repas.

    Le rechargement de l'entrée est déclenché par l'`update_listener` posé dans
    `async_setup_entry`.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Affiche et enregistre les options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_PHOTO_RETENTION, DEFAULT_PHOTO_RETENTION)
        schema = vol.Schema(
            {
                vol.Required(CONF_PHOTO_RETENTION, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=0)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
