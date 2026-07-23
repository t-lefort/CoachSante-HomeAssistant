"""Fixtures partagées par les tests de l'intégration CoachSanté."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.coachsante.const import CONF_PERSON, CONF_SECRET, DOMAIN

# Identifiants fixes réutilisés par tous les tests de webhook.
WEBHOOK_ID = "coachsante-test-webhook-id"
SECRET = "secret-hmac-de-test"


@pytest.fixture(scope="session", autouse=True)
def _prewarm_dns_resolver_thread() -> None:
    """Pré-démarre le thread daemon de pycares (résolveur DNS d'aiohttp).

    Ce thread est créé paresseusement à la première requête HTTP, donc *après*
    que le harness Home Assistant a photographié les threads vivants : il passe
    alors pour une fuite au premier test qui touche le webhook. En le lançant une
    fois pour toutes ici, il fait partie du décor commun à tous les tests.
    """
    try:
        import pycares
    except ImportError:
        return
    pycares.Channel()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Autorise le chargement de `custom_components/coachsante` pendant les tests."""
    return


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """Entrée de configuration « Thomas », prête à être ajoutée à hass."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Thomas",
        unique_id="thomas",
        data={
            CONF_PERSON: "Thomas",
            CONF_WEBHOOK_ID: WEBHOOK_ID,
            CONF_SECRET: SECRET,
        },
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> MockConfigEntry:
    """Ajoute et configure l'entrée « Thomas »."""
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    return mock_entry


def sign(body: bytes, secret: str = SECRET) -> dict[str, str]:
    """Header de signature HMAC-SHA256 pour un corps brut donné."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-CoachSante-Signature": f"sha256={digest}"}


def encode(payload: dict[str, Any]) -> bytes:
    """Sérialise un payload comme le fait l'app (octets signés = octets envoyés)."""
    return json.dumps(payload).encode()
