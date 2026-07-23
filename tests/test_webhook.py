"""Webhook : signature, tailles, JSON, types, métriques, photos, anti-rejeu."""

from __future__ import annotations

import base64
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.coachsante import webhook as webhook_module
from custom_components.coachsante.const import DOMAIN, EVENT_MEAL_PHOTO, EVENT_METRICS

from .conftest import WEBHOOK_ID, encode, sign

URL = f"/api/webhook/{WEBHOOK_ID}"


@pytest.fixture
async def client(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> Any:
    """Client HTTP avec l'entrée « Thomas » déjà configurée et le webhook en place."""
    return await hass_client_no_auth()


def _metric_entity_id(hass: HomeAssistant, entry_id: str, key: str) -> str | None:
    """Retrouve l'entity_id d'un capteur de métrique par son unique_id."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_metric_{key}")


# --- Signature -------------------------------------------------------------


async def test_signature_absente(client: Any) -> None:
    """Sans header de signature : 401."""
    body = encode({"type": "metrics", "metrics": []})
    resp = await client.post(URL, data=body)
    assert resp.status == 401


async def test_signature_invalide(client: Any) -> None:
    """Signature qui ne correspond pas au corps : 401."""
    body = encode({"type": "metrics", "metrics": []})
    resp = await client.post(URL, data=body, headers=sign(b"autre chose"))
    assert resp.status == 401


# --- Tailles et format -----------------------------------------------------


async def test_corps_trop_gros(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un corps au-delà de la limite : 413, avant même la signature."""
    monkeypatch.setattr(webhook_module, "MAX_PAYLOAD_BYTES", 16)
    resp = await client.post(URL, data=b"x" * 64)
    assert resp.status == 413


async def test_json_casse(client: Any) -> None:
    """Un corps qui n'est pas du JSON valide : 400."""
    body = b"{ceci n'est pas du json"
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_type_inconnu(client: Any) -> None:
    """Un `type` non géré : 400."""
    body = encode({"type": "quelque_chose"})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_corps_non_objet(client: Any) -> None:
    """Un corps JSON qui n'est pas un objet : 400."""
    body = encode([1, 2, 3])  # type: ignore[arg-type]
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


# --- Métriques -------------------------------------------------------------


async def test_metriques_catalogue_et_cle_inconnue(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Une clé du catalogue et une clé inconnue créent chacune une entité."""
    events = async_capture_events(hass, EVENT_METRICS)
    body = encode(
        {
            "type": "metrics",
            "metrics": [
                {"key": "steps", "value": 8421, "day": "2026-07-22", "source": "iPhone"},
                {"key": "un_truc_exotique", "value": 42},
            ],
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200
    assert (await resp.json())["accepted"] == 2

    await hass.async_block_till_done()
    entry_id = init_integration.entry_id

    steps_id = _metric_entity_id(hass, entry_id, "steps")
    assert steps_id is not None
    assert hass.states.get(steps_id).state == "8421"

    exotic_id = _metric_entity_id(hass, entry_id, "un_truc_exotique")
    assert exotic_id is not None
    assert hass.states.get(exotic_id).state == "42"

    assert len(events) == 1
    assert set(events[0].data["keys"]) == {"steps", "un_truc_exotique"}
    assert events[0].data["person"] == "Thomas"


async def test_metriques_pas_une_liste(client: Any) -> None:
    """`metrics` qui n'est pas une liste : 400."""
    body = encode({"type": "metrics", "metrics": {"key": "steps"}})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_metriques_entrees_invalides_ignorees(client: Any) -> None:
    """Les entrées sans clé ou sans valeur sont ignorées, pas rejetées."""
    body = encode(
        {
            "type": "metrics",
            "metrics": [
                {"key": "steps", "value": 100},
                {"key": "sans_valeur"},
                {"value": 5},
                "pas un objet",
            ],
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200
    assert (await resp.json())["accepted"] == 1


# --- Photos ----------------------------------------------------------------


async def test_photo_valide(
    hass: HomeAssistant, client: Any, init_integration: MockConfigEntry
) -> None:
    """Une photo valide est écrite sur disque, l'entité image et l'event suivent."""
    events = async_capture_events(hass, EVENT_MEAL_PHOTO)
    raw = b"\xff\xd8\xff\xe0 fausse image jpeg"
    body = encode(
        {
            "type": "meal_photo",
            "taken_at": "2026-07-22T12:35:00+02:00",
            "note": "midi",
            "photo": {
                "content_type": "image/jpeg",
                "data": base64.b64encode(raw).decode(),
            },
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200

    await hass.async_block_till_done()
    assert len(events) == 1
    event = events[0]
    assert event.data["note"] == "midi"
    assert event.data["person"] == "Thomas"

    path = Path(event.data["path"])
    assert path.is_file()
    assert path.read_bytes() == raw

    data = init_integration.runtime_data
    assert data.photo_path == str(path)


async def test_photo_base64_invalide(client: Any) -> None:
    """Un base64 cassé : 400."""
    body = encode(
        {
            "type": "meal_photo",
            "photo": {"content_type": "image/jpeg", "data": "pas du base64 %%%"},
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_photo_content_type_refuse(client: Any) -> None:
    """Un content-type d'image non supporté : 400."""
    body = encode(
        {
            "type": "meal_photo",
            "photo": {
                "content_type": "image/gif",
                "data": base64.b64encode(b"gif").decode(),
            },
        }
    )
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_photo_sans_data(client: Any) -> None:
    """Une photo sans champ `data` : 400."""
    body = encode({"type": "meal_photo", "photo": {"content_type": "image/jpeg"}})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


# --- Anti-rejeu ------------------------------------------------------------


async def test_anti_rejeu_sent_at_ancien(client: Any) -> None:
    """Un `sent_at` plus vieux que la fenêtre anti-rejeu : 400."""
    vieux = (dt_util.utcnow() - timedelta(minutes=10)).isoformat()
    body = encode({"type": "metrics", "sent_at": vieux, "metrics": []})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 400


async def test_anti_rejeu_sent_at_frais(client: Any) -> None:
    """Un `sent_at` récent passe."""
    frais = dt_util.utcnow().isoformat()
    body = encode({"type": "metrics", "sent_at": frais, "metrics": []})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200


async def test_anti_rejeu_sent_at_absent(client: Any) -> None:
    """Sans `sent_at`, on ne bloque pas (rétro-compatibilité)."""
    body = encode({"type": "metrics", "metrics": []})
    resp = await client.post(URL, data=body, headers=sign(body))
    assert resp.status == 200
