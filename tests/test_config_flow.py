"""Config flow : création d'une personne, unicité, credentials, options."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.coachsante.const import (
    CONF_PERSON,
    CONF_PHOTO_RETENTION,
    CONF_SECRET,
    DOMAIN,
)


async def test_flow_cree_une_personne(hass: HomeAssistant) -> None:
    """Le flow demande le prénom, montre URL + secret, puis crée l'entrée."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Thomas"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"

    placeholders = result["description_placeholders"]
    assert placeholders["person"] == "Thomas"
    assert placeholders["secret"]
    assert placeholders["url"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Thomas"

    data = result["data"]
    assert data[CONF_PERSON] == "Thomas"
    assert data[CONF_WEBHOOK_ID]
    # `secrets.token_hex(32)` → 64 caractères hexadécimaux.
    assert len(data[CONF_SECRET]) == 64
    # Le secret affiché est bien celui enregistré.
    assert data[CONF_SECRET] == placeholders["secret"]


async def test_flow_refuse_nom_vide(hass: HomeAssistant) -> None:
    """Un prénom vide (ou juste des espaces) est refusé avec une erreur."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PERSON: "person_empty"}


async def test_flow_slug_unique(hass: HomeAssistant) -> None:
    """Deux personnes au même slug ne peuvent pas coexister."""
    MockConfigEntry(domain=DOMAIN, unique_id="thomas", data={}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Thomas"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_retention(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """L'options flow enregistre la rétention des photos."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PHOTO_RETENTION: 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options[CONF_PHOTO_RETENTION] == 30


async def test_options_flow_refuse_negatif(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Une rétention négative est rejetée par le schéma."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    with pytest.raises(Exception):  # noqa: B017 - voluptuous.Invalid, peu importe le type exact
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PHOTO_RETENTION: -1}
        )
