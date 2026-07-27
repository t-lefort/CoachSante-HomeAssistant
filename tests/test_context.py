"""Contexte nutritionnel : webhook, services, rétention, entités."""

from __future__ import annotations

import base64
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.coachsante.const import (
    CONF_CONTEXT_RETENTION_DAYS,
    DOMAIN,
    EVENT_CONTEXT,
    EVENT_CONTEXT_PHOTO,
    EVENT_MEAL_PHOTO,
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_PROMPT_LENGTH,
    MAX_CONTEXT_TEXT_LENGTH,
    SERVICE_ADD_CONTEXT,
    SERVICE_CLEAR_CONTEXT,
)
from custom_components.coachsante.data import CoachSanteData

from .conftest import WEBHOOK_ID, encode, sign

URL = f"/api/webhook/{WEBHOOK_ID}"

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0 fausse image jpeg").decode()


@pytest.fixture
async def client(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> Any:
    """Client HTTP avec l'entrée « Thomas » configurée et son webhook en place."""
    return await hass_client_no_auth()


async def _fresh_data(hass: HomeAssistant, mock_entry: MockConfigEntry) -> CoachSanteData:
    mock_entry.add_to_hass(hass)
    data = CoachSanteData(hass, mock_entry)
    await data.async_load()
    return data


def _entity_id(hass: HomeAssistant, entry_id: str, suffix: str) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{suffix}")


# --- Webhook ---------------------------------------------------------------


async def test_contexte_texte_seul(
    hass: HomeAssistant,
    client: Any,
    init_integration: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un texte de contexte est stocké et part dans l'event `coachsante_context`."""
    async def no_recipe(*_: Any) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.coachsante.webhook.async_extract_recipe", no_recipe
    )
    events = async_capture_events(hass, EVENT_CONTEXT)
    body = encode(
        {
            "type": "context",
            "label": "Recette du soir",
            "text": "https://cookidoo.fr/recipes/recipe/fr-FR/r123456",
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200

    payload = await resp.json()
    assert payload["ok"] is True
    assert payload["path"] is None

    await hass.async_block_till_done()
    data = init_integration.runtime_data
    assert len(data.context) == 1

    item = data.context[0]
    assert item.id == payload["context_id"]
    assert item.text == "https://cookidoo.fr/recipes/recipe/fr-FR/r123456"
    assert item.label == "Recette du soir"
    assert item.pending_analysis is False
    assert "Recette du soir" in data.context_prompt

    assert len(events) == 1
    assert events[0].data["context_id"] == item.id
    assert events[0].data["person"] == "Thomas"


async def test_lien_de_recette_est_enrichi(
    hass: HomeAssistant,
    client: Any,
    init_integration: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un lien seul apporte portions et quantités au contexte du prochain repas."""
    from custom_components.coachsante.recipe import RecipeSummary

    async def recipe(*_: Any) -> RecipeSummary:
        return RecipeSummary(
            name="Gratin de chou-fleur",
            text=(
                "Recette : Gratin de chou-fleur\n"
                "Rendement : 6 portions\n"
                "Ingrédients : 1000 g de chou-fleur; 250 g de lait de coco"
            ),
        )

    monkeypatch.setattr(
        "custom_components.coachsante.webhook.async_extract_recipe", recipe
    )
    url = "https://cookidoo.fr/recipes/recipe/fr-FR/r825145"
    body = encode({"type": "context", "text": url})

    response = await client.post(URL, data=body, headers=sign(body))

    assert response.status == 200
    item = init_integration.runtime_data.context[0]
    assert item.label == "Gratin de chou-fleur"
    assert item.text.startswith(url)
    assert "6 portions" in item.text
    assert "1000 g de chou-fleur" in item.text


async def test_contexte_photo_demande_son_analyse(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Une photo de contexte est rangée à part et attend son analyse."""
    events = async_capture_events(hass, EVENT_CONTEXT_PHOTO)
    body = encode(
        {
            "type": "context",
            "captured_at": "2026-07-25T19:59:00+02:00",
            "label": "Paquet de raviolis",
            "photo": {"content_type": "image/jpeg", "data": JPEG},
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200

    await hass.async_block_till_done()
    assert len(events) == 1
    event = events[0]

    path = Path(event.data["path"])
    assert path.is_file()
    # Rangée dans le sous-dossier de contexte, pas avec les photos de repas.
    assert path.parent.name == "contexte"

    data = init_integration.runtime_data
    item = data.context[0]
    assert item.id == event.data["context_id"]
    assert item.pending_analysis is True
    assert item.photo_path == str(path)
    # Rien d'exploitable tant que l'analyse n'est pas revenue.
    assert data.context_prompt == ""
    assert data.context_pending_count == 1


async def test_contexte_texte_et_photo(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Texte et photo ensemble : le texte compte déjà, la photo reste à analyser."""
    body = encode(
        {
            "type": "context",
            "text": "portion pour deux",
            "photo": {"content_type": "image/jpeg", "data": JPEG},
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200

    await hass.async_block_till_done()
    item = init_integration.runtime_data.context[0]
    assert item.text == "portion pour deux"
    assert item.pending_analysis is True
    assert "portion pour deux" in init_integration.runtime_data.context_prompt


async def test_contexte_vide_refuse(client: Any) -> None:
    """Ni texte ni photo : 400."""
    body = encode({"type": "context", "label": "rien"})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_contexte_texte_blanc_refuse(client: Any) -> None:
    """Un texte qui ne contient que des espaces ne vaut pas un contexte : 400."""
    body = encode({"type": "context", "text": "   "})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_contexte_base64_invalide(client: Any) -> None:
    """Une photo de contexte au base64 cassé : 400."""
    body = encode(
        {
            "type": "context",
            "photo": {"content_type": "image/jpeg", "data": "pas du base64 %%%"},
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_photo_repas_embarque_le_contexte(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """L'event d'une photo de repas porte le contexte à injecter dans le prompt."""
    context = encode({"type": "context", "text": "riz complet, 120 g crus"})
    assert (await client.post(URL, data=context, headers=sign(context))).status == 200

    events = async_capture_events(hass, EVENT_MEAL_PHOTO)
    meal = encode(
        {
            "type": "meal_photo",
            "note": "midi, au boulot",
            "photo": {"content_type": "image/jpeg", "data": JPEG},
        }
    )
    assert (await client.post(URL, data=meal, headers=sign(meal))).status == 200

    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["note"] == "midi, au boulot"
    assert "riz complet, 120 g crus" in events[0].data["context"]


# --- Services --------------------------------------------------------------


async def test_add_context_cree_un_element(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Sans `context_id`, le service crée un élément de contexte."""
    events = async_capture_events(hass, EVENT_CONTEXT)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_CONTEXT,
        {
            "entry_id": init_integration.entry_id,
            "text": "pain de campagne : 250 kcal / 100 g",
            "label": "Pain",
        },
        blocking=True,
    )

    data = init_integration.runtime_data
    assert len(data.context) == 1
    assert data.context[0].label == "Pain"
    assert len(events) == 1


async def test_add_context_complete_une_photo(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Avec `context_id`, le texte devient l'analyse de la photo correspondante."""
    photo_events = async_capture_events(hass, EVENT_CONTEXT_PHOTO)
    body = encode({"type": "context", "photo": {"content_type": "image/jpeg", "data": JPEG}})
    assert (await client.post(URL, data=body, headers=sign(body))).status == 200
    await hass.async_block_till_done()

    context_id = photo_events[0].data["context_id"]
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_CONTEXT,
        {
            "entry_id": init_integration.entry_id,
            "context_id": context_id,
            "text": "Raviolis ricotta-épinards : 232 kcal / 100 g",
            "label": "Raviolis",
        },
        blocking=True,
    )

    data = init_integration.runtime_data
    item = data.context[0]
    assert item.id == context_id
    assert item.pending_analysis is False
    assert item.label == "Raviolis"
    assert "232 kcal" in data.context_prompt
    assert data.context_pending_count == 0
    # Toujours un seul élément : l'analyse complète, elle ne duplique pas.
    assert len(data.context) == 1


async def test_add_context_rejoue_ne_duplique_pas(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Une automatisation qui rejoue son appel remplace l'analyse, sans doublon."""
    data = init_integration.runtime_data
    item = data.add_context(photo_path="/media/coachsante/thomas/contexte/x.jpg")

    for texte in ("première lecture", "seconde lecture"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONTEXT,
            {"entry_id": init_integration.entry_id, "context_id": item.id, "text": texte},
            blocking=True,
        )

    assert len(data.context) == 1
    assert data.context[0].analysis == "seconde lecture"


async def test_add_context_id_inconnu(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Un `context_id` expiré ou inventé lève une erreur de validation."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_CONTEXT,
            {
                "entry_id": init_integration.entry_id,
                "context_id": "nexiste_pas",
                "text": "analyse orpheline",
            },
            blocking=True,
        )


async def test_clear_context_un_element_efface_sa_photo(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Retirer un élément efface aussi la photo qu'il portait."""
    events = async_capture_events(hass, EVENT_CONTEXT_PHOTO)
    body = encode({"type": "context", "photo": {"content_type": "image/jpeg", "data": JPEG}})
    assert (await client.post(URL, data=body, headers=sign(body))).status == 200
    await hass.async_block_till_done()

    path = Path(events[0].data["path"])
    assert path.is_file()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_CONTEXT,
        {"entry_id": init_integration.entry_id, "context_id": events[0].data["context_id"]},
        blocking=True,
    )

    assert init_integration.runtime_data.context == []
    assert not path.exists()


async def test_clear_context_tout(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Sans `context_id`, tout le contexte de la personne est oublié."""
    data = init_integration.runtime_data
    data.add_context(text="un")
    data.add_context(text="deux")

    await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_CONTEXT, {"entry_id": init_integration.entry_id}, blocking=True
    )

    assert data.context == []


# --- Rétention et bornes ---------------------------------------------------


async def test_retention_deux_semaines(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Au-delà de la rétention, l'élément et sa photo disparaissent."""
    data = await _fresh_data(hass, mock_entry)
    photo = data.context_dir / "vieille.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"jpeg")

    data.add_context(
        text="périmé", photo_path=str(photo), at=dt_util.now() - timedelta(days=15)
    )
    data.add_context(text="encore bon", at=dt_util.now() - timedelta(days=13))

    assert await data.async_purge_expired_context() is True
    assert [item.text for item in data.context] == ["encore bon"]
    assert not photo.exists()


async def test_retention_zero_garde_tout(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Une rétention à 0 jour signifie « on ne jette jamais »."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_CONTEXT_RETENTION_DAYS: 0})
    data = CoachSanteData(hass, mock_entry)
    await data.async_load()

    data.add_context(text="très vieux", at=dt_util.now() - timedelta(days=400))

    assert await data.async_purge_expired_context() is False
    assert len(data.context) == 1


async def test_trop_plein_sacrifie_les_plus_anciens(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Au-delà de la borne d'éléments, les plus anciens sautent."""
    data = await _fresh_data(hass, mock_entry)
    for index in range(MAX_CONTEXT_ITEMS + 5):
        data.add_context(text=f"élément {index}", at=dt_util.now() - timedelta(minutes=index))

    await data.async_purge_expired_context()

    assert len(data.context) == MAX_CONTEXT_ITEMS
    # Le plus ancien (minutes=MAX+4) a sauté, le plus récent (minutes=0) est là.
    assert data.context[-1].text == "élément 0"


async def test_texte_borne_en_longueur(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Un texte trop long est tronqué à l'entrée, pas à l'affichage."""
    data = await _fresh_data(hass, mock_entry)
    item = data.add_context(text="a" * (MAX_CONTEXT_TEXT_LENGTH + 500))

    assert len(item.text) == MAX_CONTEXT_TEXT_LENGTH


async def test_prompt_borne_garde_les_plus_recents(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Un prompt trop long est tronqué en sacrifiant les vieux éléments."""
    data = await _fresh_data(hass, mock_entry)
    for index in range(10):
        data.add_context(
            text=f"{index} " + "x" * 1000, at=dt_util.now() - timedelta(hours=10 - index)
        )

    prompt = data.context_prompt

    assert len(prompt) <= MAX_CONTEXT_PROMPT_LENGTH
    assert "9 xxx" in prompt  # le plus récent
    assert "0 xxx" not in prompt  # le plus ancien a sauté


async def test_contexte_survit_au_redemarrage(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Le contexte est rechargé depuis le store après un redémarrage."""
    data = await _fresh_data(hass, mock_entry)
    item = data.add_context(text="pâtes complètes", label="Placard")
    data.add_context(photo_path="/media/coachsante/thomas/contexte/a.jpg")
    await data._store.async_save(data._as_dict())

    reloaded = CoachSanteData(hass, mock_entry)
    await reloaded.async_load()

    assert len(reloaded.context) == 2
    assert reloaded.context[0].id == item.id
    assert reloaded.context[0].label == "Placard"
    assert reloaded.context_pending_count == 1


# --- Entités ---------------------------------------------------------------


async def test_capteur_contexte(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Le capteur compte les éléments et expose le prompt en attribut."""
    body = encode({"type": "context", "text": "quinoa : 120 kcal / 100 g cuits"})
    assert (await client.post(URL, data=body, headers=sign(body))).status == 200
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, init_integration.entry_id, "context")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state.state == "1"
    assert "quinoa" in state.attributes["prompt"]
    assert state.attributes["en_attente_analyse"] == 0
    assert state.attributes["retention_jours"] == 14
    assert state.attributes["elements"][0]["extrait"] == "quinoa : 120 kcal / 100 g cuits"


async def test_capteur_note_de_repas(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """La note jointe à la photo devient visible dans Home Assistant."""
    body = encode(
        {
            "type": "meal_photo",
            "note": "midi, au boulot",
            "photo": {"content_type": "image/jpeg", "data": JPEG},
        }
    )
    assert (await client.post(URL, data=body, headers=sign(body))).status == 200
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, init_integration.entry_id, "meal_note")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "midi, au boulot"

    # La même note est lisible sur l'entité image, à côté de la photo.
    image_id = init_integration.runtime_data.image_entity_id
    assert hass.states.get(image_id).attributes["note"] == "midi, au boulot"
