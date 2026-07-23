"""Services `add_nutrition` et `reset_day`."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.coachsante.const import (
    DOMAIN,
    EVENT_NUTRITION,
    SERVICE_ADD_NUTRITION,
    SERVICE_RESET_DAY,
)


async def test_add_nutrition_cumule(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Deux appels successifs cumulent les macros et émettent l'event."""
    entry_id = init_integration.entry_id
    events = async_capture_events(hass, EVENT_NUTRITION)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_NUTRITION,
        {"entry_id": entry_id, "energy_kcal": 200, "protein_g": 10, "label": "midi"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_NUTRITION,
        {"entry_id": entry_id, "energy_kcal": 300},
        blocking=True,
    )

    data = init_integration.runtime_data
    assert data.nutrition["energy_kcal"] == 500
    assert data.nutrition["protein_g"] == 10
    assert len(events) == 2
    assert events[0].data["added"] == {"energy_kcal": 200.0, "protein_g": 10.0}
    assert events[1].data["totals"]["energy_kcal"] == 500


async def test_reset_day(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """`reset_day` remet les compteurs à zéro."""
    entry_id = init_integration.entry_id
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_NUTRITION,
        {"entry_id": entry_id, "energy_kcal": 400},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, SERVICE_RESET_DAY, {"entry_id": entry_id}, blocking=True
    )

    data = init_integration.runtime_data
    assert data.nutrition["energy_kcal"] == 0
    assert data.meals == []


async def test_add_nutrition_sans_nutriment(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Appeler `add_nutrition` sans aucun nutriment lève une erreur."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_NUTRITION,
            {"entry_id": init_integration.entry_id, "label": "vide"},
            blocking=True,
        )


async def test_add_nutrition_entree_inconnue(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Un `entry_id` inconnu lève une erreur de validation de service."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_NUTRITION,
            {"entry_id": "entree_qui_nexiste_pas", "energy_kcal": 100},
            blocking=True,
        )
